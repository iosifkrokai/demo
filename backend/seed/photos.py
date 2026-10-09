"""Photo hints from OSM, and the licensed pictures they resolve to.

Two stages and one apply, kept apart on purpose so the heavy PBF read and the
Wikimedia calls never share a step:

  1. ``collect_hints()`` — scan an OSM extract (PBF) and keep only the photo tags
     (``wikidata``, ``wikipedia``, ``wikimedia_commons``, ``image``) of the
     objects our points claim to be (``places.source_url`` → ``<type>/<id>``),
     writing ``data/osm_photo_hints.json``. This is the only stage that reads the
     PBF, and the only one that needs pyosmium — imported lazily, so importing
     this module (and ``python -m seed``) does not require it.

  2. ``resolve_photos()`` — turn each hint into at most one picture through
     Commons/Wikidata/Wikipedia, writing ``data/place_photos.json``. This is the
     only stage that talks to the network; its HTTP answers are cached on disk
     (``$PHOTO_CACHE``) so a second run is nearly free.

  3. ``apply_to_db()`` — the ``--apply`` path: write ``photo_url``,
     ``photo_author``, ``photo_license`` and ``photo_source`` onto ``places``,
     and withdraw a photo the file no longer vouches for.

Only images we can point at are ever written. Deliberate refusals, each of which
costs coverage:

  * **No attribution, no photo.** A record missing its author or licence is
    dropped rather than shown — an unattributed picture is worse than none.
  * **Commons only.** The OSM ``image`` tag also holds share links
    (``photos.app.goo.gl`` and friends); a URL outside ``upload.wikimedia.org`` /
    ``commons.wikimedia.org`` has no metadata we can honestly show.
  * **A category is not a photo** (``Category:…``, or a path such as
    ``Belarus/Grodno/Farny``) — a category lists other things, so taking its
    first member would be a guess.
  * **A URL is verified, not assumed** — the content type has to come back
    ``image/*``.

Run from ``backend/``::

    python -m seed.photos --hints           # 1. PBF → osm_photo_hints.json
    python -m seed.photos --limit 20        # 2. hints → place_photos.json (probe)
    python -m seed.photos                   # 2. hints → place_photos.json
    python -m seed.photos --apply           # 3. …and into the DB
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psycopg

BACKEND = Path(__file__).resolve().parent.parent
HINTS = BACKEND / "data" / "osm_photo_hints.json"
OUT = BACKEND / "data" / "place_photos.json"
CACHE = Path(os.environ.get("PHOTO_CACHE", "/home/codespace/.hermes/cache/scratch/photo_cache"))
DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

#: The Belarus extract, when the caller passes none — ``OSM_PBF`` or ``--pbf``.
#: Deliberately no baked-in path: a checkout names its own extract.
PBF = os.environ.get("OSM_PBF")

UA = "GrodnoGuide/1.0 (tourist guide for Grodno Oblast; image attribution)"
THUMB_WIDTH = 800
SLEEP = 1.0  # politeness between API calls; Wikimedia is a donation-funded service
#: 429 is the one that actually happens here; 5xx is worth the same patience.
#: A long tail on purpose: giving up on a batch loses the pictures of every
#: article in it, and the disk cache makes a patient retry nearly free to redo.
RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF = (5.0, 15.0, 45.0, 90.0, 180.0)
RETRIES = len(BACKOFF) + 1

_OSM_REF = re.compile(r"^osm(?:_poi)?:([a-z]+/\d+)$")
#: How far an article's own coordinates may sit from our point and still count
#: as the same place. Wide enough for a big park or a castle grounds, far too
#: narrow for a different town with a church of the same name.
MAX_MATCH_M = 2000
# Uploads live only here; anything else has no metadata we can honestly show.
WIKIMEDIA_HOSTS = ("upload.wikimedia.org", "commons.wikimedia.org")

#: The photo-ish OSM tags a hint may hold, kept verbatim, nothing inferred.
WANTED = ("wikidata", "wikipedia", "webpage", "wikimedia_commons", "image")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — the PBF read (the only one), writing osm_photo_hints.json
# ─────────────────────────────────────────────────────────────────────────────


def _osmium() -> Any:
    """Import pyosmium on demand, with a clear error when it is not installed.

    It is deliberately not a declared dependency (and not imported at module
    scope), so a checkout that only resolves photos from an existing hints file
    can import this module — and ``python -m seed`` — without it.
    """
    try:
        return importlib.import_module("osmium")
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "стадия подсказок требует pyosmium (osmium): он не объявлен в pyproject — установите его отдельно (pip install osmium) или запустите стадию resolve по уже собранному osm_photo_hints.json"
        ) from exc


def load_source_urls(dsn: str | None = None) -> list[str]:
    """Every ``places.source_url``, read from the DB rather than typed in.

    The hints stage keeps only the objects these URLs claim to be, so the join
    is exact (``osm:node/306067583`` → ``node/306067583``) and no name or
    coordinate fuzzy-matching is involved.
    """
    with psycopg.connect(dsn or DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_url FROM places")
        return [r[0] for r in cur.fetchall()]


def collect_hints(
    pbf_path: Path,
    source_urls: Iterable[str],
    *,
    out: Path,
) -> dict[str, dict[str, str]]:
    """Keep the photo tags OSM states for the objects our points *are*.

    The full Belarus extract carries ~50k objects with some photo-ish tag while
    the guide has ~3.7k points, ~450 of which line up with one, so committing the
    whole scan would put a 3 MB file in the repo for nothing. ``source_urls`` is
    the set of ``places.source_url`` values; only the ``<type>/<id>`` identities
    they claim survive. The output is keyed ``<type>/<id>`` and sorted, so a
    re-run on the same PBF produces a byte-identical file.

    pyosmium is imported here, not at module scope: it is not a declared
    dependency, and a checkout that only ever resolves photos from an existing
    hints file must still be able to import this module.
    """
    osmium: Any = _osmium()

    wanted: set[str] = {m.group(1) for url in source_urls if (m := _OSM_REF.match(url or ""))}
    print(f"точек с OSM-ссылкой в source_url: {len(wanted)}")

    SimpleHandler = osmium.SimpleHandler

    class Collector(SimpleHandler):  # type: ignore[misc]
        """One pass over the extract, keeping only the objects we asked for."""

        def __init__(self) -> None:
            super().__init__()
            self.found: dict[str, dict[str, str]] = {}

        def _keep(self, kind: str, obj: Any) -> None:
            key = f"{kind}/{obj.id}"
            if key not in wanted:
                return
            tags = {t.k: t.v for t in obj.tags}
            hit = {k: tags[k] for k in WANTED if k in tags}
            if hit:
                self.found[key] = hit

        def node(self, n: Any) -> None:
            self._keep("node", n)

        def way(self, w: Any) -> None:
            self._keep("way", w)

        def relation(self, r: Any) -> None:
            self._keep("relation", r)

    collector = Collector()
    collector.apply_file(str(pbf_path))

    found = {k: collector.found[k] for k in sorted(collector.found)}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(found, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")

    tags = Counter(t for v in found.values() for t in v)
    print(f"объектов с подсказкой среди наших точек: {len(found)}  {dict(tags)}")
    print(f"без ответа (в OSM подсказки нет): {len(wanted) - len(found)}")
    print(f"-> {_shown(out)}")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — hints → Wikimedia/Wikidata → place_photos.json (the only network)
# ─────────────────────────────────────────────────────────────────────────────


def api(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ask a MediaWiki API, POSTing the query.

    The query travels in the body: fifty Cyrillic article titles URL-encode into
    a request line long enough for the server to answer 414, and that happened
    here for real. The disk cache is still keyed on the equivalent GET string, so
    answers fetched by an earlier run stay cached.
    """
    body = urllib.parse.urlencode(params).encode()
    return http_json(endpoint, post=body, cache_id=f"{endpoint}?{body.decode()}")


def http_json(url: str, post: bytes | None = None, cache_id: str | None = None) -> dict[str, Any]:
    """GET (or POST) a JSON API with a disk cache, retries and backoff.

    Wikimedia answers 429 when asked too fast, and a reseed that dies halfway is
    worse than a slow one — so a rate limit is waited out (honouring
    `Retry-After` when it is sent) instead of propagating. Cached answers make a
    second run cheap, which is what makes the retries affordable at all.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (hashlib.sha256((cache_id or url).encode()).hexdigest()[:24] + ".json")
    if key.exists():
        try:
            return json.loads(key.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            key.unlink()

    last: Exception | None = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(
            url, data=post, headers={"User-Agent": UA, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            key.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            time.sleep(SLEEP)
            return payload
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in RETRY_STATUS:
                raise
            wait = (
                float(exc.headers.get("Retry-After") or 0)
                or BACKOFF[min(attempt, len(BACKOFF) - 1)]
            )
            print(f"  {exc.code} от Wikimedia — жду {wait:.0f} с и повторяю")
            time.sleep(wait)
        except Exception as exc:
            last = exc
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise last if last else RuntimeError(url)


def is_image(url: str) -> str:
    """Does this URL serve an image: 'image', 'not_image', or 'unknown'.

    One short attempt only. Commons named the file, so this is a sanity check
    rather than the evidence, and a host that is slow or rate-limiting us must
    not stretch a reseed into hours — the caller treats 'unknown' as "Commons
    vouched, the check did not answer" and says so in the data.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as resp:
            ctype = resp.headers.get("Content-Type", "")
            return "image" if ctype.startswith("image/") else "not_image"
    except urllib.error.HTTPError as exc:
        if exc.code in RETRY_STATUS:
            return "unknown"
        # A definite "no such file / not an image" — believe it.
        return "not_image"
    except Exception:
        return "unknown"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres — how far an article's own coordinates sit from ours."""
    from math import asin, cos, radians, sin, sqrt

    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371000 * asin(sqrt(a))


def page_meta(wiki: str, titles: list[str]) -> dict[str, dict[str, Any]]:
    """Each article's coordinates and Wikidata id, batched, redirects followed.

    Half of our hand-written titles are redirects («Коложская церковь» →
    «Борисоглебская церковь»), and the API returns a redirect stub with no
    properties at all. Following them is not a nicety here: without it the
    article looks like it has no coordinates and the point loses its photo.
    The answer is keyed by BOTH the requested and the resolved title, so a
    caller can look up the name it asked for.
    """
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i : i + 40]
        data = api(
            f"https://{wiki}.wikipedia.org/w/api.php",
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "coordinates|pageprops",
                "ppprop": "wikibase_item",
                "redirects": 1,
                "format": "json",
            },
        )
        query = data.get("query", {}) or {}
        alias: dict[str, str] = {}
        for item in (query.get("normalized", []) or []) + (query.get("redirects", []) or []):
            alias[item["from"]] = item["to"]

        resolved: dict[str, dict[str, Any]] = {}
        for page in (query.get("pages", {}) or {}).values():
            coords = (page.get("coordinates") or [{}])[0]
            resolved[page.get("title", "")] = {
                "lat": coords.get("lat"),
                "lon": coords.get("lon"),
                "qid": (page.get("pageprops", {}) or {}).get("wikibase_item"),
            }
        for title in chunk:
            wanted = title
            seen: set[str] = set()
            while wanted in alias and wanted not in seen:
                seen.add(wanted)
                wanted = alias[wanted]
            info = resolved.get(wanted)
            if info:
                out[title] = info
    return out


def article_from_links(raw: str | None) -> tuple[str, str] | None:
    """The Wikipedia article a hand-authored row already points at.

    The curated dataset carries its own source links, so the article is a fact
    of our data rather than a guess at a famous name — and the whole search step
    disappears with it (which also stops us hammering the search API).
    """
    items: list[dict[str, Any]] = []
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith(("[", "{")):
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            items = [parsed]
        elif isinstance(parsed, list):
            items = [x for x in parsed if isinstance(x, dict)]
    else:
        items = [{"url": part.strip()} for part in raw.split("|") if part.strip()]

    for item in items:
        url = str(item.get("url", ""))
        m = re.match(r"https?://([a-z-]{2,12})\.wikipedia\.org/wiki/(.+)$", url)
        if not m:
            continue
        wiki, title = m.group(1), urllib.parse.unquote(m.group(2))
        title = title.split("#", 1)[0].replace("_", " ").strip()
        if wiki and title:
            return wiki, title
    return None


def is_settlement_article(place: dict[str, Any], title: str) -> bool:
    """Is this the article about the town rather than about the place in it?

    Some of our curated links point at the settlement («Гродно» for Sovetskaya
    street), which sits a kilometre away and would pass a distance check while
    illustrating the wrong thing. Our own `town` field names the settlement, so
    the comparison is data against data, not a guess.
    """
    town = (place.get("town") or "").strip()
    if not town:
        return False
    return title.strip().lower() == town.lower()


def match_article(place: dict[str, Any], meta: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Keep the candidate article only if its own coordinates agree with ours.

    The name is not evidence — «Костёл Святого Михаила Архангела» is five
    different churches — so a candidate without coordinates, or one further than
    `MAX_MATCH_M` away, is dropped. A wrong photo is worse than none.
    """
    for title, info in meta.items():
        if is_settlement_article(place, title):
            continue
        if info.get("lat") is None or info.get("lon") is None:
            continue
        distance = haversine_m(place["lat"], place["lon"], info["lat"], info["lon"])
        if distance <= MAX_MATCH_M:
            return {
                "article": f"{info.get('wiki', 'ru')}:{title}",
                "qid": info.get("qid"),
                "match_m": round(distance),
            }
    return None


def commons_file_title(hint: str) -> str | None:
    """`File:X.jpg` → `File:X.jpg`; a category, gallery or path is not a photo."""
    hint = (hint or "").strip()
    if hint.lower().startswith("file:"):
        return hint
    # `Category:…`, `File:…` in another namespace, or a path such as
    # `Belarus/Grodno/Farny` — none of these name one image file.
    if ":" in hint or "/" in hint:
        return None
    return f"File:{hint}"


def clean_author(raw: str) -> str:
    """The Artist field is wikitext-rendered HTML, and it shows.

    Two shapes occur in the real data and both look like bugs in a UI:
    a name with the raw link in parentheses ("Валацуга (https://fgb.by/view/1)"),
    and a template that renders twice ("Unknown authorUnknown author").
    A credit line is a name, so the link and the copy are stripped here.
    """
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"\s*\((?:https?://|www\.)[^)]*\)?", " ", text)
    text = re.sub(r"\s*<https?://[^>]*>?", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # "FooFoo" — the same credit emitted twice by the source template.
    half = len(text) // 2
    if len(text) >= 8 and len(text) % 2 == 0 and text[:half] == text[half:]:
        text = text[:half].strip()
    # "Namehttps://…" — a link glued to the name with no separator.
    text = re.sub(r"(https?://\S+)$", "", text).strip()
    return text


def commons_imageinfo(titles: list[str]) -> dict[str, dict[str, Any]]:
    """One call per 50 files: URL, author, licence, file page."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i : i + 50]
        data = api(
            "https://commons.wikimedia.org/w/api.php",
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|size",
                "iiurlwidth": THUMB_WIDTH,
                "format": "json",
            },
        )
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            if not info.get("url"):
                continue
            meta = info.get("extmetadata", {}) or {}

            out[page.get("title", "")] = {
                "url": info.get("thumburl") or info.get("url"),
                "original": info.get("url"),
                "author": _extmetadata_value(meta, "Artist") or _extmetadata_value(meta, "Credit"),
                "license": _extmetadata_value(meta, "LicenseShortName"),
                "source": info.get("descriptionurl")
                or (meta.get("DescriptionUrl", {}) or {}).get("value", ""),
                "width": info.get("thumbwidth") or info.get("width"),
                "height": info.get("thumbheight") or info.get("height"),
            }
    return out


def _extmetadata_value(meta: dict[str, Any], name: str) -> str:
    """One field of a Commons extmetadata block, cleaned for display.

    A module-level helper rather than a closure over the loop's `meta`: the
    value is passed in, so nothing can bind to a variable that is about to be
    rebound on the next page.
    """
    return clean_author((meta.get(name, {}) or {}).get("value", "") or "")


def _first_claim(claims: dict[str, Any], prop: str) -> Any:
    """The first non-empty value of a Wikidata claim property, or None."""
    for claim in claims.get(prop, []) or []:
        value = (claim.get("mainsnak", {}) or {}).get("datavalue", {}).get("value")
        if value:
            return value
    return None


def wikidata_claims(qids: list[str]) -> dict[str, dict[str, Any]]:
    """Q-id → its image and its own coordinates, in one batched call.

    P18 (image) and P625 (coordinates) come from the same request, and P625 is
    the reliable half: the wiki's own GeoData answers for fewer articles than
    Wikidata does, and a point can be verified against Wikidata even when the
    article carries no coordinate of its own.
    """
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(qids), 50):
        chunk = qids[i : i + 50]
        data = api(
            "https://www.wikidata.org/w/api.php",
            {
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": "claims",
                "format": "json",
            },
        )
        for qid, entity in (data.get("entities", {}) or {}).items():
            claims = (entity or {}).get("claims", {}) or {}

            coords = _first_claim(claims, "P625")
            out[qid] = {
                "image": _first_claim(claims, "P18"),
                "lat": (coords or {}).get("latitude") if isinstance(coords, dict) else None,
                "lon": (coords or {}).get("longitude") if isinstance(coords, dict) else None,
            }
    return out


def wikipedia_pageimage(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """(wiki, article) → the article's lead image file name, grouped per wiki.

    A hint names a wiki we cannot vouch for, so an unreachable or nonsense code
    is treated as "no image" rather than allowed to break the whole reseed.
    """
    by_wiki: dict[str, list[str]] = {}
    for wiki, title in pairs:
        by_wiki.setdefault(wiki, []).append(title)

    out: dict[tuple[str, str], str] = {}
    for wiki, titles in by_wiki.items():
        for i in range(0, len(titles), 50):
            chunk = titles[i : i + 50]
            try:
                data = api(
                    f"https://{wiki}.wikipedia.org/w/api.php",
                    {
                        "action": "query",
                        "titles": "|".join(chunk),
                        "prop": "pageimages",
                        "piprop": "name",
                        "format": "json",
                    },
                )
            except Exception as exc:
                print(f"  вики {wiki}: {type(exc).__name__} — пропускаю")
                continue
            for page in (data.get("query", {}).get("pages", {}) or {}).values():
                name = page.get("pageimage")
                if name and page.get("title"):
                    out[(wiki, page["title"])] = f"File:{name}"
    return out


def parse_wikipedia(hint: str) -> tuple[str, str] | None:
    """`be:Ніжняя царква (Гродна)#Section` → ('be', 'Ніжняя царква (Гродна)')."""
    if ":" not in hint:
        return None
    wiki, _, title = hint.partition(":")
    title = title.split("#", 1)[0].strip()
    if not wiki or not title or not re.fullmatch(r"[a-z-]{2,12}", wiki):
        return None
    return wiki, title


def load_places(dsn: str | None = None) -> list[dict[str, Any]]:
    with psycopg.connect(dsn or DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source_url, name, lat, lon, links, town FROM places ORDER BY source_url"
        )
        return [
            {
                "source_url": r[0],
                "name": r[1],
                "lat": r[2],
                "lon": r[3],
                "links": r[4],
                "town": r[5],
            }
            for r in cur.fetchall()
        ]


def resolve_photos(
    hints: dict[str, dict[str, Any]] | None = None,
    places: list[dict[str, Any]] | None = None,
    *,
    out: Path | None = OUT,
    limit: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve every hint to at most one credited picture.

    ``hints`` and ``places`` default to the shipped hints file and the live DB,
    so a bare ``resolve_photos()`` is the whole pass; passing them in keeps the
    resolution rules testable without either. The result is written to ``out``
    (``data/place_photos.json``) unless it is ``None``, sorted so a reseed is
    diffable, and returned as well.
    """
    if hints is None:
        hints = json.loads(HINTS.read_text(encoding="utf-8"))
    assert hints is not None  # narrowing for the type checker; json.loads is never None
    if places is None:
        places = load_places()
    stats: Counter[str] = Counter()

    # Only the points whose OSM object carries a hint are worth any call at all.
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for place in places:
        m = _OSM_REF.match(place["source_url"] or "")
        if not m:
            stats["без OSM-ссылки"] += 1
            continue
        hint = hints.get(m.group(1))
        if not hint:
            stats["в OSM нет подсказки"] += 1
            continue
        candidates.append((place, hint))

    # Hand-authored points carry `city:`/`region:` keys, so there is no OSM
    # object to look up — and these are exactly the highlights a tourist sees
    # first. They get a second, verifiable path below: the article's own
    # coordinates have to agree with ours.
    named = [
        place
        for place in places
        if not _OSM_REF.match(place["source_url"] or "")
        and place["source_url"].startswith(("city:", "region:"))
    ]
    print(f"авторских точек для поиска по названию: {len(named)}")

    if limit:
        candidates = candidates[:limit]
    print(f"кандидатов: {len(candidates)}")

    # ── batch the network work before touching any place ────────────────────
    qids = [h["wikidata"] for _, h in candidates if h.get("wikidata")]
    claims = wikidata_claims(sorted(set(qids))) if qids else {}
    with_image = sum(1 for c in claims.values() if c.get("image"))
    print(
        f"Wikidata ответила по {len(claims)} из {len(set(qids))} объектов, картинка есть у {with_image}"
    )

    wiki_pairs = [
        parsed
        for _, h in candidates
        if h.get("wikipedia") and (parsed := parse_wikipedia(h["wikipedia"]))
    ]
    pageimages = wikipedia_pageimage(sorted(set(wiki_pairs))) if wiki_pairs else {}
    print(f"Википедия дала лид-картинку: {len(pageimages)} из {len(set(wiki_pairs))}")

    wanted: dict[str, tuple[str, str]] = {}  # source_url -> (file title, via)
    #: How a hand-authored point was matched to its article, for review.
    provenance: dict[str, dict[str, Any]] = {}

    # ── pick a file title per point, strongest hint first ───────────────────
    for place, hint in candidates:
        title = via = None
        if hint.get("wikimedia_commons"):
            title = commons_file_title(hint["wikimedia_commons"])
            via = "wikimedia_commons"
        claim = claims.get(hint.get("wikidata") or "") or {}
        if not title and claim.get("image"):
            title, via = f"File:{claim['image']}", "wikidata"
        if not title and hint.get("wikipedia"):
            parsed = parse_wikipedia(hint["wikipedia"])
            if parsed and pageimages.get(parsed):
                title, via = pageimages[parsed], "wikipedia"
        if title:
            wanted[place["source_url"]] = (title, via or "unknown")
        elif hint.get("image"):
            stats["только ссылка-поделиться, атрибуции нет"] += 1
        else:
            stats["подсказка не дала файла"] += 1

    # ── hand-authored points: our own source link, verified by coordinates ──

    for place in named:
        article = article_from_links(place.get("links"))
        if not article:
            stats["авторская точка: ссылки на Википедию нет"] += 1
            continue
        place["article"] = article
    with_article = [p for p in named if p.get("article")]

    meta: dict[str, dict[str, Any]] = {}
    for wiki in sorted({p["article"][0] for p in with_article}):
        titles = [p["article"][1] for p in with_article if p["article"][0] == wiki]
        for title, info in page_meta(wiki, titles).items():
            meta[f"{wiki}:{title}"] = info
    print(
        f"авторских точек со ссылкой: {len(with_article)}, ответ по статье получен для {len(meta)}"
    )

    # Wikidata holds the image and the coordinates for the same item, so the
    # whole hand-authored set costs one extra call rather than two per point.
    article_qids = [info["qid"] for info in meta.values() if info.get("qid")]
    if article_qids:
        claims.update(wikidata_claims(sorted(set(article_qids))))

    matched: dict[str, dict[str, Any]] = {}
    for place in with_article:
        wiki, title = place["article"]
        info = meta.get(f"{wiki}:{title}")
        if not info:
            stats["авторская точка: статьи нет в вики"] += 1
            continue
        qid = info.get("qid")
        claim = claims.get(qid or "") or {}
        # Wikidata's own coordinate first: the wiki answers for fewer articles
        # than Wikidata does, and a redirect stub answers for none.
        lat = claim.get("lat") if claim.get("lat") is not None else info.get("lat")
        lon = claim.get("lon") if claim.get("lon") is not None else info.get("lon")
        match = match_article(place, {title: {"lat": lat, "lon": lon, "qid": qid, "wiki": wiki}})
        if not match:
            stats["авторская точка: координаты не совпали"] += 1
            continue
        matched[place["source_url"]] = {**match, "place": place, "image": claim.get("image")}
    print(f"подтверждено координатами: {len(matched)} из {len(named)}")

    article_pairs = []
    for m in matched.values():
        parsed = parse_wikipedia(m["article"])
        if parsed:
            article_pairs.append(parsed)
    if article_pairs:
        pageimages.update(wikipedia_pageimage(sorted(set(article_pairs))))

    for source_url, match in matched.items():
        title = via = None
        if match.get("image"):
            title, via = f"File:{match['image']}", "wikidata"
        if not title:
            parsed = parse_wikipedia(match["article"])
            if parsed and pageimages.get(parsed):
                title, via = pageimages[parsed], "wikipedia"
        if title:
            wanted[source_url] = (title, via or "unknown")
            provenance[source_url] = {
                "article": match["article"],
                "match_m": match["match_m"],
            }
        else:
            stats["авторская точка: у статьи нет картинки"] += 1

    info = commons_imageinfo(sorted({t for t, _ in wanted.values()})) if wanted else {}
    print(f"Commons ответил по файлам: {len(info)} из {len({t for t, _ in wanted.values()})}")

    photos: dict[str, dict[str, Any]] = {}
    for source_url, (title, via) in sorted(wanted.items()):
        entry = info.get(title) or info.get(title.replace("_", " "))
        if not entry or not entry.get("url"):
            stats[f"файла нет на Commons ({via})"] += 1
            continue
        # Attribution is not optional: a file we cannot credit is a file we skip.
        if not entry.get("author") or not entry.get("license"):
            stats["без автора или лицензии"] += 1
            continue
        if not any(host in entry["url"] for host in WIKIMEDIA_HOSTS):
            stats["картинка не на Wikimedia"] += 1
            continue
        verdict = is_image(entry["url"])
        if verdict == "not_image":
            stats["ссылка не отдала картинку"] += 1
            continue
        if verdict == "unknown":
            # Commons named the file and the host is its upload server; a HEAD
            # that went unanswered is not evidence the picture is missing.
            stats["проверку не подтвердили, но файл назван Commons"] += 1
        photos[source_url] = {
            "url": entry["url"],
            "author": entry["author"],
            "license": entry["license"],
            "source": entry["source"],
            "width": entry.get("width"),
            "height": entry.get("height"),
            "via": via,
            "verified": verdict,
            **provenance.get(source_url, {}),
        }

    print("\nитог:")
    for key, value in stats.most_common():
        print(f"  {key}: {value}")
    print(f"  с фото: {len(photos)}")

    if out is not None:
        out.write_text(
            json.dumps(photos, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"-> {_shown(out)}  ({len(photos)} точек)")
    return photos


# ─────────────────────────────────────────────────────────────────────────────
# Apply — write the file onto the DB, withdrawal included
# ─────────────────────────────────────────────────────────────────────────────


def apply_to_db(conn: psycopg.Connection, photos: dict[str, dict[str, Any]]) -> int:
    """Write the file into the DB, and take away what the file no longer has.

    The JSON is the source of truth: a point whose photo the resolution pass no
    longer stands behind must lose it here too, otherwise the database keeps
    showing a picture that the data file refuses to vouch for.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE places
                  SET photo_url = NULL, photo_author = NULL,
                      photo_license = NULL, photo_source = NULL
                WHERE photo_url IS NOT NULL
                  AND source_url <> ALL(%s)""",
            (list(photos),),
        )
        withdrawn = cur.rowcount
        for source_url, photo in photos.items():
            cur.execute(
                """UPDATE places
                      SET photo_url = %s, photo_author = %s,
                          photo_license = %s, photo_source = %s
                    WHERE source_url = %s""",
                (
                    photo["url"],
                    photo["author"],
                    photo["license"],
                    photo["source"],
                    source_url,
                ),
            )
        conn.commit()
        cur.execute("SELECT count(*) FROM places WHERE photo_url IS NOT NULL")
        row = cur.fetchone()
        total = row[0] if row else 0
        if withdrawn:
            print(f"снято фото, которых больше нет в файле: {withdrawn}")
        return total


# ─────────────────────────────────────────────────────────────────────────────
# One entry for a caller that runs the stages in sequence
# ─────────────────────────────────────────────────────────────────────────────


def run_stage(
    *,
    stage: str,
    data_dir: Path,
    pbf_path: Path | None = None,
    limit: int | None = None,
    apply: bool = False,
    dsn: str | None = None,
) -> int:
    """Run the photo pipeline stages, all three kept out of import time.

    ``stage`` is one of ``{"hints", "resolve", "all"}``:

      * ``hints``/``all`` regenerate ``data_dir/osm_photo_hints.json`` from the
        PBF (``pbf_path`` or ``$OSM_PBF``; a clear error if neither is set, and
        ``collect_hints`` names pyosmium if it is missing);
      * ``resolve``/``all`` turn the hints into ``data_dir/place_photos.json``;
      * with ``apply=True`` the result is also written into the DB (``dsn`` or
        ``$DATABASE_URL``), withdrawal included.

    ``limit`` caps how many points the resolve pass processes. Returns what the
    stage produced/wrote: the hint count for ``hints``, the photo count for
    ``resolve``/``all``. Nothing here imports osmium or opens a DB until called.
    """
    if stage not in {"hints", "resolve", "all"}:
        raise ValueError(f"неизвестная стадия {stage!r}: ожидается hints/resolve/all")

    hints_path = data_dir / "osm_photo_hints.json"
    photos_path = data_dir / "place_photos.json"

    if stage in {"hints", "all"}:
        pbf = pbf_path or (Path(PBF) if PBF else None)
        if pbf is None:
            raise RuntimeError("стадия hints требует PBF: передайте pbf_path или задайте OSM_PBF")
        # Fail on a missing pyosmium before opening the DB for source_urls.
        _osmium()
        hints = collect_hints(pbf, load_source_urls(dsn), out=hints_path)
    else:
        if not hints_path.exists():
            raise FileNotFoundError(f"нет {hints_path} — сначала стадия hints")
        hints = json.loads(hints_path.read_text(encoding="utf-8"))

    if stage == "hints":
        return len(hints)

    photos = resolve_photos(hints, load_places(dsn), out=photos_path, limit=limit)

    if apply:
        with psycopg.connect(dsn or DSN) as conn:
            apply_to_db(conn, photos)

    return len(photos)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _shown(path: Path) -> Path | str:
    """A path relative to the backend, for a log line — the path itself if not."""
    try:
        return path.relative_to(BACKEND)
    except ValueError:
        return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hints", action="store_true", help="стадия 1: собрать подсказки из PBF")
    ap.add_argument("--pbf", type=Path, default=None, help="PBF-экстракт (по умолчанию $OSM_PBF)")
    ap.add_argument("--limit", type=int, default=None, help="только первые N точек")
    ap.add_argument("--apply", action="store_true", help="записать в базу")
    args = ap.parse_args(argv)

    try:
        count = run_stage(
            stage="hints" if args.hints else "resolve",
            data_dir=BACKEND / "data",
            pbf_path=args.pbf,
            limit=args.limit,
            apply=args.apply,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.apply:
        print(f"в базе с фото: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
