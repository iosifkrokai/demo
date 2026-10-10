"""Photo hints from OSM, and the licensed pictures they resolve to.
Stages: collect hints from a PBF, resolve them via Wikimedia, then apply to the DB.
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

from core.paths import PHOTOS_DIR
from db.connection import connect

BACKEND = Path(__file__).resolve().parent.parent
HINTS = PHOTOS_DIR / "osm_photo_hints.json"
OUT = PHOTOS_DIR / "place_photos.json"
CACHE = Path(os.environ.get("PHOTO_CACHE", "/home/codespace/.hermes/cache/scratch/photo_cache"))
DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

PBF = os.environ.get("OSM_PBF")

UA = "GrodnoGuide/1.0 (tourist guide for Grodno Oblast; image attribution)"
THUMB_WIDTH = 800
SLEEP = 1.0
RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF = (5.0, 15.0, 45.0, 90.0, 180.0)
RETRIES = len(BACKOFF) + 1

_OSM_REF = re.compile(r"^osm(?:_poi)?:([a-z]+/\d+)$")
MAX_MATCH_M = 2000
WIKIMEDIA_HOSTS = ("upload.wikimedia.org", "commons.wikimedia.org")

WANTED = ("wikidata", "wikipedia", "webpage", "wikimedia_commons", "image")


def _osmium() -> Any:
    """Import pyosmium on demand, with a clear error when it is not installed.
    Not a declared dependency, so a hints-file-only checkout can still import this.
    """
    try:
        return importlib.import_module("osmium")
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "стадия подсказок требует pyosmium (osmium): он не объявлен в pyproject — установите его отдельно (pip install osmium) или запустите стадию resolve по уже собранному osm_photo_hints.json"
        ) from exc


def load_source_urls(dsn: str | None = None) -> list[str]:
    """Every ``places.source_url``, read from the DB rather than typed in.
    The hints stage keeps only the exact objects these URLs claim to be.
    """
    with connect(dsn or DSN, timeout=None) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_url FROM places")
        return [r[0] for r in cur.fetchall()]


def collect_hints(
    pbf_path: Path,
    source_urls: Iterable[str],
    *,
    out: Path,
) -> dict[str, dict[str, str]]:
    """Keep the photo tags OSM states for the objects our points *are*.
    Only the ``<type>/<id>`` identities in ``source_urls`` survive; output is sorted.
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


def api(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ask a MediaWiki API, POSTing the query.
    The body avoids 414 on long Cyrillic titles; cache is keyed on the GET string.
    """
    body = urllib.parse.urlencode(params).encode()
    return http_json(endpoint, post=body, cache_id=f"{endpoint}?{body.decode()}")


def http_json(url: str, post: bytes | None = None, cache_id: str | None = None) -> dict[str, Any]:
    """GET (or POST) a JSON API with a disk cache, retries and backoff.
    Rate limits are waited out (honouring ``Retry-After``), not propagated.
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
    One short attempt; 'unknown' means the check did not answer.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as resp:
            ctype = resp.headers.get("Content-Type", "")
            return "image" if ctype.startswith("image/") else "not_image"
    except urllib.error.HTTPError as exc:
        if exc.code in RETRY_STATUS:
            return "unknown"
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
    Answer is keyed by both the requested and the resolved title.
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
    Using the row's own source links removes the search step entirely.
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
    Compares the article title against the row's own ``town`` field.
    """
    town = (place.get("town") or "").strip()
    if not town:
        return False
    return title.strip().lower() == town.lower()


def match_article(place: dict[str, Any], meta: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Keep the candidate article only if its own coordinates agree with ours.
    A candidate without coordinates, or further than ``MAX_MATCH_M``, is dropped.
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
    if ":" in hint or "/" in hint:
        return None
    return f"File:{hint}"


def clean_author(raw: str) -> str:
    """Clean a Commons Artist/Credit field, which is wikitext-rendered HTML.
    Strips links, duplicated text and a trailing URL.
    """
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"\s*\((?:https?://|www\.)[^)]*\)?", " ", text)
    text = re.sub(r"\s*<https?://[^>]*>?", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    half = len(text) // 2
    if len(text) >= 8 and len(text) % 2 == 0 and text[:half] == text[half:]:
        text = text[:half].strip()
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
    """One field of a Commons extmetadata block, cleaned for display."""
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
    P625 is the reliable half; a point can be verified against it.
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
    An unreachable or nonsense wiki code yields "no image", not a crash.
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
    with connect(dsn or DSN, timeout=None) as conn, conn.cursor() as cur:
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
    ``hints``/``places`` default to the shipped file and live DB; result is returned.
    """
    if hints is None:
        hints = json.loads(HINTS.read_text(encoding="utf-8"))
    assert hints is not None
    if places is None:
        places = load_places()
    stats: Counter[str] = Counter()

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

    wanted: dict[str, tuple[str, str]] = {}
    provenance: dict[str, dict[str, Any]] = {}

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


def apply_to_db(conn: psycopg.Connection, photos: dict[str, dict[str, Any]]) -> int:
    """Write the file into the DB, and take away what the file no longer has.
    The JSON is the source of truth, so a withdrawn photo is cleared here too.
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


def run_stage(
    *,
    stage: str,
    data_dir: Path | None = None,
    pbf_path: Path | None = None,
    limit: int | None = None,
    apply: bool = False,
    dsn: str | None = None,
) -> int:
    """Run the photo pipeline stages, all three kept out of import time.
    ``stage`` is one of ``{"hints", "resolve", "all"}``; returns the count produced.
    """
    if stage not in {"hints", "resolve", "all"}:
        raise ValueError(f"неизвестная стадия {stage!r}: ожидается hints/resolve/all")

    hints_path = (data_dir or PHOTOS_DIR) / "osm_photo_hints.json"
    photos_path = (data_dir or PHOTOS_DIR) / "place_photos.json"

    if stage in {"hints", "all"}:
        pbf = pbf_path or (Path(PBF) if PBF else None)
        if pbf is None:
            raise RuntimeError("стадия hints требует PBF: передайте pbf_path или задайте OSM_PBF")
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
        with connect(dsn or DSN, timeout=None) as conn:
            apply_to_db(conn, photos)

    return len(photos)


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
            data_dir=PHOTOS_DIR,
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
