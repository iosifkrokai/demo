"""Versioned datasets, their validation, and the coverage report.
Pure and offline: reads the committed CSVs and never touches a DB or network.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import constants as _constants
from core.paths import (
    EXCLUDED_NAMES_JSON,
    PLACE_ALIASES_JSON,
    PLACE_MERGES_JSON,
    PLACES_DIR,
    TOWN_NAMES_JSON,
)

from . import taxonomy
from .geofence import inside_project_area
from .osm_tags import is_fallback_name

SOURCE_CURATED = "curated"
SOURCE_DATASET = "dataset"
SOURCE_AUTO = "auto"
PROTECTED_CATEGORY_SOURCES = (SOURCE_CURATED, SOURCE_DATASET)

GEOFENCE_PROBLEM = "outside Grodno region"

DEFAULT_DUP_RADIUS_M = _constants.DUPLICATE_RADIUS_M

SAME_OBJECT_RADIUS_M = 30.0

TWIN_RADIUS_M = 5.0

CURATED_TWIN_RADIUS_M = 2000.0

COLUMNS = [
    "name",
    "category",
    "district",
    "town",
    "lat",
    "lon",
    "blurb",
    "fun_fact",
    "fun_facts",
    "opening_hours",
    "ticket_price",
    "visit_minutes",
    "links",
    "source_url",
]

BBOX = {"south": 52.75, "west": 23.35, "north": 55.01, "east": 27.00}

FINDS_DATASET = "finds"

SIGHT_TAXONOMY = frozenset(c.code for c in taxonomy.all_categories() if c.role == "sight")
SERVICE_TAXONOMY = frozenset(c.code for c in taxonomy.all_categories() if c.role == "service")
STEPS_TAXONOMY = frozenset(
    c.code for c in taxonomy.all_categories() if "highway=steps" in c.osm_tags
)


MIN_BUCKET_ROWS = 2


def load_excluded_names(path: Path | None = None) -> frozenset[str]:
    """The names no dataset may carry, normalised for comparison; read from
    ``excluded_names.json``, a judgement written down rather than a pattern guessed.
    """
    source = path or EXCLUDED_NAMES_JSON
    if not source.is_file():
        return frozenset()
    data = json.loads(source.read_text(encoding="utf-8"))
    return frozenset(norm_name(entry.get("name")) for entry in data.get("names") or [])


def is_excluded_name(name: str | None, excluded: frozenset[str] | None = None) -> bool:
    """True when ``name`` is on the exclusion list."""
    known = excluded if excluded is not None else load_excluded_names()
    normalised = norm_name(name)
    return bool(normalised) and normalised in known


def load_town_names(path: Path | None = None) -> dict[str, str]:
    """Belarusian → Russian settlement names from the committed mapping; a value the file
    does not list is left alone, since a wrong town is worse than a Belarusian one.
    """
    source = path or TOWN_NAMES_JSON
    if not source.is_file():
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
    return {str(was): str(now) for was, now in (data.get("names") or {}).items()}


def read_place_alias_entries(path: Path | None = None) -> list[dict[str, str]]:
    source = path or PLACE_ALIASES_JSON
    if not source.is_file():
        return []
    data = json.loads(source.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for entry in data.get("places") or []:
        url = str(entry.get("source_url") or "")
        locale = str(entry.get("locale") or "ru")
        if not url:
            continue
        rows.extend(
            {"source_url": url, "locale": locale, "alias": str(name).strip()}
            for name in entry.get("names") or []
            if str(name).strip()
        )
    return rows


def load_place_aliases(path: Path | None = None) -> dict[str, frozenset[str]]:
    """Those names normalised, by ``source_url``: a protected row is bucketed under every
    name it answers to, so it can shadow an automatic row that spells the place differently.
    """
    out: dict[str, set[str]] = {}
    for row in read_place_alias_entries(path):
        key = norm_name(row["alias"])
        if key:
            out.setdefault(row["source_url"], set()).add(key)
    return {url: frozenset(names) for url, names in out.items()}


def load_declared_merges(path: Path | None = None) -> list[dict[str, str]]:
    """The pairs of rows that are one object, as ``place_merges.json`` declares them."""
    source = path or PLACE_MERGES_JSON
    if not source.is_file():
        return []
    data = json.loads(source.read_text(encoding="utf-8"))
    return [
        {"keep": str(entry["keep"]), "drop": str(entry["drop"])}
        for entry in data.get("pairs") or []
        if entry.get("keep") and entry.get("drop")
    ]


def read_pipe_csv(path: Path) -> list[dict[str, Any]]:
    """Parse a pipe-delimited CSV whose header may be commented out."""
    lines = [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    rows: list[dict[str, Any]] = []
    for raw in csv.reader(lines, delimiter="|"):
        if len(raw) != len(COLUMNS):
            raise SystemExit(f"expected {len(COLUMNS)} columns, got {len(raw)}: {raw[:3]}...")
        rows.append(dict(zip(COLUMNS, raw, strict=False)))
    return rows


def _validate_common(
    row: dict[str, Any],
    *,
    taxonomy_codes: frozenset[str],
    prefixes: tuple[str, ...],
    visit_required: bool,
) -> list[str]:
    problems: list[str] = []
    if row["category"] not in taxonomy_codes:
        problems.append(f"category {row['category']!r} not in taxonomy")
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except (ValueError, TypeError):
        return ["lat/lon not numeric"]
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return ["lat/lon not finite"]
    if not inside_project_area(lat, lon):
        problems.append("coordinates outside Grodno region")
    if not (BBOX["south"] <= lat <= BBOX["north"]):
        problems.append(f"lat {lat} outside bbox")
    if not (BBOX["west"] <= lon <= BBOX["east"]):
        problems.append(f"lon {lon} outside bbox")
    for key in ("fun_facts", "links"):
        if (row.get(key) or "").strip():
            try:
                json.loads(row[key])
            except json.JSONDecodeError as e:
                problems.append(f"bad JSON in {key} ({e})")
    if not row["source_url"].startswith(prefixes):
        problems.append(f"source_url must start with one of {prefixes}")
    visit = (row.get("visit_minutes") or "").strip()
    if visit_required or visit:
        try:
            int(visit)
        except (ValueError, TypeError):
            problems.append(f"visit_minutes {row['visit_minutes']!r} not an int")
    return problems


def _normalize_common(row: dict[str, Any], *, visit_required: bool) -> dict[str, Any]:
    visit = (row.get("visit_minutes") or "").strip()
    return {
        "name": row["name"].strip(),
        "category": row["category"].strip() or None,
        "district": row["district"].strip() or None,
        "town": row["town"].strip() or None,
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "blurb": row["blurb"].strip() or None,
        "fun_fact": row["fun_fact"].strip() or None,
        "fun_facts": row["fun_facts"].strip() or None,
        "opening_hours": row["opening_hours"].strip() or None,
        "ticket_price": row["ticket_price"].strip() or None,
        "visit_minutes": int(visit) if visit else (0 if visit_required else None),
        "links": row["links"].strip() or None,
        "source_url": row["source_url"].strip(),
    }


def validate_city_region(row: dict[str, Any]) -> list[str]:
    """city:/region: hand-authored rows — sights, visit_minutes required."""
    return _validate_common(
        row, taxonomy_codes=SIGHT_TAXONOMY, prefixes=("city:", "region:"), visit_required=True
    )


def validate_osm_sight(row: dict[str, Any]) -> list[str]:
    """osm: rows — sights, visit_minutes required."""
    return _validate_common(
        row, taxonomy_codes=SIGHT_TAXONOMY, prefixes=("osm:",), visit_required=True
    )


def validate_osm_service(row: dict[str, Any]) -> list[str]:
    """osm_poi: rows — everyday services, visit_minutes optional (NULL)."""
    return _validate_common(
        row, taxonomy_codes=SERVICE_TAXONOMY, prefixes=("osm_poi:",), visit_required=False
    )


def validate_osm_steps(row: dict[str, Any]) -> list[str]:
    """osm: rows — stairways and the like, visit_minutes optional (NULL)."""
    return _validate_common(
        row, taxonomy_codes=STEPS_TAXONOMY, prefixes=("osm:",), visit_required=False
    )


def normalize_city_region(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=True)


def normalize_osm_sight(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=True)


def normalize_osm_service(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=False)


def normalize_osm_steps(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=False)


@dataclass
class Dataset:
    """One versioned CSV dataset with its loader and natural-key prefix."""

    name: str
    filename: str
    prefix: str
    reader: Callable[[Path], list[dict[str, Any]]]
    validator: Callable[[dict[str, Any]], list[str]]
    normalizer: Callable[[dict[str, Any]], dict[str, Any]]
    category_source: str
    fatal_invalid: bool
    license: str = "see file header"
    optional: bool = False
    path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.path = PLACES_DIR / self.filename

    def with_data_dir(self, data_dir: Path) -> Dataset:
        self.path = data_dir / self.filename
        return self


def default_datasets() -> list[Dataset]:
    """The CSV datasets, in apply order (hand-authored, then OSM)."""
    return [
        Dataset(
            "city",
            filename="places_grodno_city.csv",
            prefix="city:",
            reader=read_pipe_csv,
            validator=validate_city_region,
            normalizer=normalize_city_region,
            category_source=SOURCE_DATASET,
            fatal_invalid=True,
            license="hand-authored (planetabelarus.by derived)",
        ),
        Dataset(
            "region",
            filename="places_region.csv",
            prefix="region:",
            reader=read_pipe_csv,
            validator=validate_city_region,
            normalizer=normalize_city_region,
            category_source=SOURCE_DATASET,
            fatal_invalid=True,
            license="hand-authored (planetabelarus.by derived)",
        ),
        Dataset(
            "osm",
            filename="places_osm_raw.csv",
            prefix="osm:",
            reader=read_pipe_csv,
            validator=validate_osm_sight,
            normalizer=normalize_osm_sight,
            category_source=SOURCE_AUTO,
            fatal_invalid=False,
            license="ODbL 1.0 (OpenStreetMap contributors)",
        ),
        Dataset(
            "poi",
            filename="places_poi.csv",
            prefix="osm_poi:",
            reader=read_pipe_csv,
            validator=validate_osm_service,
            normalizer=normalize_osm_service,
            category_source=SOURCE_AUTO,
            fatal_invalid=False,
            optional=True,
            license="ODbL 1.0 (OpenStreetMap contributors)",
        ),
        Dataset(
            "steps",
            filename="places_steps.csv",
            prefix="osm:",
            reader=read_pipe_csv,
            validator=validate_osm_steps,
            normalizer=normalize_osm_steps,
            category_source=SOURCE_AUTO,
            fatal_invalid=False,
            license="ODbL 1.0 (OpenStreetMap contributors)",
        ),
        Dataset(
            FINDS_DATASET,
            filename="places_finds.csv",
            prefix="osm:",
            reader=read_pipe_csv,
            validator=validate_osm_sight,
            normalizer=normalize_osm_sight,
            category_source=SOURCE_AUTO,
            fatal_invalid=False,
            optional=True,
            license="ODbL 1.0 (OpenStreetMap contributors)",
        ),
    ]


CURATED_PREFIX = 5


def _json_link(token: str) -> dict[str, str] | None:
    try:
        value = json.loads(token)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict) and isinstance(value.get("url"), str):
        return {"title": str(value.get("title") or value["url"]), "url": value["url"]}
    return None


def _curated_links(tail: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Split the variadic tail into extra facts and the links that end it."""
    links: list[dict[str, str]] = []
    index = len(tail)
    while index:
        token = tail[index - 1].strip()
        link = _json_link(token)
        if link is not None:
            links.insert(0, link)
            index -= 1
            continue
        if token.startswith(("http://", "https://")):
            title = tail[index - 2].strip() if index > 1 else ""
            if title and len(title.split()) == 1:
                links.insert(0, {"title": title, "url": token})
                index -= 2
            else:
                links.insert(0, {"title": token, "url": token})
                index -= 1
            continue
        break
    facts = [token.strip() for token in tail[:index] if token.strip()]
    return facts, links


def read_curated(path: Path) -> list[dict[str, Any]]:
    """Parse the curated file: five fixed columns, then facts and links sharing the tail."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < CURATED_PREFIX or not fields[0].strip().isdigit():
            raise SystemExit(f"unexpected curated row: {line[:80]}")
        facts, links = _curated_links(fields[CURATED_PREFIX:])
        rows.append(
            {
                "id": int(fields[0]),
                "name": fields[1].strip(),
                "category": fields[2].strip() or None,
                "blurb": fields[3].strip() or None,
                "fun_fact": fields[4].strip() or None,
                "fun_facts": "|".join(facts) or None,
                "links": json.dumps(links, ensure_ascii=False) if links else None,
            }
        )
    return rows


def _widest_gap_m(rows: list[dict[str, Any]]) -> float:
    """The widest distance between any two of the rows, or 0 for a single row."""
    widest = 0.0
    for index, first in enumerate(rows):
        for second in rows[index + 1 :]:
            gap = haversine_m(first["lat"], first["lon"], second["lat"], second["lon"])
            widest = max(widest, gap)
    return widest


def match_place(name: str, db_rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """The row a curated name means, and how: ``exact`` | ``contains`` | ``fuzzy`` |
    ``ambiguous`` | ``none``. Equality first, then containment; rows answering to one name
    are resolved by evidence, and namesakes far apart answer ``ambiguous``."""
    n = name.lower()
    ordered = sorted(db_rows, key=lambda r: (r.get("category_source") != SOURCE_DATASET, r["id"]))
    named = [r for r in ordered if r["name"].strip().lower() == n]
    if named:
        hand = [r for r in named if r.get("category_source") == SOURCE_DATASET]
        if hand:
            return hand[0], "exact"
        if len(named) == 1 or _widest_gap_m(named) <= CURATED_TWIN_RADIUS_M:
            return named[0], "exact"
        return None, "ambiguous"
    contains = [r for r in ordered if n in r["name"].lower()]
    if len(contains) == 1:
        return contains[0], "contains"
    stripped = n.replace("бывший", "").replace("в гродно", "").strip()
    fuzzy = [r for r in ordered if stripped in r["name"].lower()]
    if fuzzy:
        return fuzzy[0], "fuzzy"
    return None, "none"


def load_dataset(ds: Dataset) -> list[dict[str, Any]]:
    """Read one dataset's raw pipe rows (empty for a missing optional dataset)."""
    if not ds.path.exists():
        if ds.optional:
            return []
        raise FileNotFoundError(f"{ds.name}: dataset not found: {ds.path}")
    return ds.reader(ds.path)


def classify_reject(problems: list[str]) -> str:
    """'geofence' when the point is outside the project area, else 'invalid'."""
    return "geofence" if any(GEOFENCE_PROBLEM in p for p in problems) else "invalid"


def collect_records(datasets: list[Dataset]) -> dict[str, Any]:
    """Validate every dataset.
    Returns records, rejects (quarantined), per-dataset meta and the fatal flag.
    """
    records: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    datasets_meta: list[dict[str, Any]] = []
    fatal = False

    for ds in datasets:
        raw_rows = load_dataset(ds)
        missing = not raw_rows and ds.optional and not ds.path.exists()
        valid = invalid = geofence = 0
        for raw in raw_rows:
            problems = ds.validator(raw)
            if problems:
                kind = classify_reject(problems)
                if kind == "geofence":
                    geofence += 1
                else:
                    invalid += 1
                rejects.append(
                    {
                        "dataset": ds.name,
                        "name": str(raw.get("name", "")).strip(),
                        "source_url": str(raw.get("source_url", "")).strip(),
                        "kind": kind,
                        "problems": problems,
                    }
                )
                if ds.fatal_invalid:
                    fatal = True
                continue
            row = ds.normalizer(raw)
            row["_dataset"] = ds.name
            row["_category_source"] = ds.category_source
            records.append(row)
            valid += 1
        datasets_meta.append(
            {
                "name": ds.name,
                "file": ds.filename,
                "rows": len(raw_rows),
                "valid": valid,
                "invalid": invalid,
                "geofence_rejects": geofence,
                "category_source": ds.category_source,
                "license": ds.license,
                "missing": missing,
            }
        )

    return {"records": records, "rejects": rejects, "datasets": datasets_meta, "fatal": fatal}


_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЎў]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_NORM_RE = re.compile(r"[^0-9a-zа-яёіў]+")


def script_of(name: str | None) -> str:
    """'ru' | 'en' | 'mixed' | 'unknown' by the scripts present in a name."""
    if not name:
        return "unknown"
    has_cyr = bool(_CYRILLIC_RE.search(name))
    has_lat = bool(_LATIN_RE.search(name))
    if has_cyr and has_lat:
        return "mixed"
    if has_cyr:
        return "ru"
    if has_lat:
        return "en"
    return "unknown"


def norm_name(name: str | None) -> str:
    """Normalised name for duplicate clustering (case/space/punct-insensitive)."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name).lower()
    return _NORM_RE.sub(" ", n).strip()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two lat/lon points."""
    r = _constants.EARTH_RADIUS_M
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def find_suspected_duplicates(
    records: list[dict[str, Any]], radius_m: float = DEFAULT_DUP_RADIUS_M
) -> list[dict[str, Any]]:
    """Record pairs sharing a normalised name and category within ``radius_m``; generated
    labels are skipped. Candidates for review, not proof, and never dropped.
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in records:
        if is_fallback_name(r.get("name")):
            continue
        key = norm_name(r.get("name"))
        if not key or r.get("lat") is None or r.get("lon") is None:
            continue
        buckets.setdefault((key, str(r.get("category") or "")), []).append(r)

    pairs: list[dict[str, Any]] = []
    for (key, category), rows in buckets.items():
        if len(rows) < MIN_BUCKET_ROWS:
            continue
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if d < radius_m:
                    pairs.append(
                        {
                            "name": key,
                            "category": category,
                            "distance_m": round(d, 1),
                            "a": {"dataset": a["_dataset"], "source_url": a["source_url"]},
                            "b": {"dataset": b["_dataset"], "source_url": b["source_url"]},
                        }
                    )
    return pairs


def count_generated_labels(records: list[dict[str, Any]]) -> int:
    """Rows whose name is a generated service label rather than a name."""
    return sum(1 for r in records if is_fallback_name(r.get("name")))


def _nearest_within(
    candidates: Sequence[dict[str, Any]], lat: float, lon: float, radius_m: float
) -> dict[str, Any] | None:
    """The closest candidate within ``radius_m``, or None."""
    best: dict[str, Any] | None = None
    best_m = radius_m
    for candidate in candidates:
        distance = haversine_m(lat, lon, candidate["lat"], candidate["lon"])
        if distance < best_m:
            best, best_m = candidate, distance
    return best


def _answered_names(record: dict[str, Any], aliases: dict[str, frozenset[str]]) -> frozenset[str]:
    """The normalised names a row answers to: its own, and the ones it is declared under."""
    names = {norm_name(record.get("name"))}
    names.discard("")
    return frozenset(names | set(aliases.get(str(record.get("source_url")), ())))


def resolve_shadowed(
    records: list[dict[str, Any]],
    radius_m: float = DEFAULT_DUP_RADIUS_M,
    aliases: dict[str, frozenset[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split records into those to project and the automatic ones a hand-authored row
    shadows: same name within ``radius_m``, the hand-authored row's own or an alias's.
    """
    known = aliases or {}
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r.get("_category_source") not in PROTECTED_CATEGORY_SOURCES:
            continue
        if r.get("lat") is None or r.get("lon") is None:
            continue
        for key in _answered_names(r, known):
            buckets.setdefault(key, []).append(r)

    shadowed: list[dict[str, Any]] = []
    dropped: set[int] = set()
    for index, r in enumerate(records):
        if r.get("_category_source") in PROTECTED_CATEGORY_SOURCES:
            continue
        key = norm_name(r.get("name"))
        if not key or r.get("lat") is None or r.get("lon") is None:
            continue
        winner = _nearest_within(buckets.get(key) or (), r["lat"], r["lon"], radius_m)
        if winner is None:
            continue
        dropped.add(index)
        shadowed.append(
            {
                "name": r.get("name"),
                "dataset": r["_dataset"],
                "source_url": r["source_url"],
                "shadowed_by": winner["source_url"],
                "distance_m": round(
                    haversine_m(r["lat"], r["lon"], winner["lat"], winner["lon"]), 1
                ),
                "rule": "hand-authored",
            }
        )
    return [r for index, r in enumerate(records) if index not in dropped], shadowed


TWIN_FIELDS = (
    "blurb",
    "fun_fact",
    "fun_facts",
    "opening_hours",
    "ticket_price",
    "visit_minutes",
    "links",
)


def _richness(r: dict[str, Any]) -> int:
    return sum(1 for f in TWIN_FIELDS if r.get(f))


def _twin_groups(records: list[dict[str, Any]], radius_m: float) -> list[list[int]]:
    """Index groups of records that sit within ``radius_m`` of their group's head."""
    remaining = list(range(len(records)))
    groups: list[list[int]] = []
    while remaining:
        head, group, rest = remaining[0], [remaining[0]], []
        for index in remaining[1:]:
            a, b = records[head], records[index]
            if haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) < radius_m:
                group.append(index)
            else:
                rest.append(index)
        remaining = rest
        if len(group) > 1:
            groups.append(group)
    return groups


def resolve_twins(
    records: list[dict[str, Any]], radius_m: float = TWIN_RADIUS_M
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collapse automatic records mapping one object twice: same name and category, metres
    apart — distance tells a twin from a namesake.
    """
    buckets: dict[tuple[str, str], list[int]] = {}
    for index, r in enumerate(records):
        if r.get("_category_source") != SOURCE_AUTO:
            continue
        key = (norm_name(r.get("name")), str(r.get("category") or ""))
        if key[0] and r.get("lat") is not None and r.get("lon") is not None:
            buckets.setdefault(key, []).append(index)

    dropped: set[int] = set()
    shadowed: list[dict[str, Any]] = []
    for indexes in buckets.values():
        if len(indexes) < MIN_BUCKET_ROWS:
            continue
        rows = [records[index] for index in indexes]
        for group in _twin_groups(rows, radius_m):
            winner = min(group, key=lambda i: (-_richness(rows[i]), i))
            survivor = rows[winner]
            for position in group:
                if position == winner:
                    continue
                loser = rows[position]
                dropped.add(indexes[position])
                shadowed.append(
                    {
                        "name": loser.get("name"),
                        "dataset": loser["_dataset"],
                        "source_url": loser["source_url"],
                        "shadowed_by": survivor["source_url"],
                        "distance_m": round(
                            haversine_m(
                                loser["lat"], loser["lon"], survivor["lat"], survivor["lon"]
                            ),
                            1,
                        ),
                        "rule": "same object",
                    }
                )
    return [r for index, r in enumerate(records) if index not in dropped], shadowed


def resolve_declared_merges(
    records: list[dict[str, Any]], declared: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Drop the losing side of each declared pair, returning the records, the drops and the
    declarations the data no longer holds — a stale declaration is reported, not ignored.
    """
    index_of = {str(r.get("source_url")): index for index, r in enumerate(records)}
    dropped: set[int] = set()
    shadowed: list[dict[str, Any]] = []
    stale: list[str] = []
    for pair in declared:
        keep_at = index_of.get(pair["keep"])
        drop_at = index_of.get(pair["drop"])
        if keep_at is None or drop_at is None:
            stale.append(f"{pair['keep']} ← {pair['drop']}")
            continue
        keeper, loser = records[keep_at], records[drop_at]
        if loser.get("_category_source") in PROTECTED_CATEGORY_SOURCES:
            stale.append(f"{pair['keep']} ← {pair['drop']} (the row is hand-authored)")
            continue
        dropped.add(drop_at)
        shadowed.append(
            {
                "name": loser.get("name"),
                "dataset": loser["_dataset"],
                "source_url": loser["source_url"],
                "shadowed_by": keeper["source_url"],
                "distance_m": round(
                    haversine_m(loser["lat"], loser["lon"], keeper["lat"], keeper["lon"]), 1
                ),
                "rule": "declared",
            }
        )
    kept = [r for index, r in enumerate(records) if index not in dropped]
    return kept, shadowed, stale


def collapse(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """The records the seed projects, with the three collapse rules applied in order so a
    stage naming rows by identity sees the set the projection will.
    """
    kept, hand = resolve_shadowed(records, aliases=load_place_aliases())
    kept, twins = resolve_twins(kept)
    kept, declared, stale = resolve_declared_merges(kept, load_declared_merges())
    return kept, [*hand, *twins, *declared], stale


def _share(part: int, total: int) -> dict[str, Any]:
    return {"count": part, "total": total, "share": round(part / total, 4) if total else None}


def _counts(values: dict[str, Any], total: int) -> dict[str, Any]:
    out = {str(k): v for k, v in values.items()}
    out["_total"] = total
    return out


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


@dataclass(frozen=True)
class CoverageContext:
    """What a coverage report is assembled in, beyond the rows themselves."""

    mode: str
    curated: list[dict[str, Any]] | None = None
    curated_stats: dict[str, Any] | None = None
    duplicate_radius_m: float = DEFAULT_DUP_RADIUS_M
    db_stats: dict[str, Any] | None = None
    data_version: str | None = None
    generated_at: str | None = None
    merged: list[dict[str, Any]] = field(default_factory=list)
    stale_merges: list[str] = field(default_factory=list)


def build_coverage_report(collected: dict[str, Any], context: CoverageContext) -> dict[str, Any]:
    """Assemble the machine-readable coverage report from in-memory records."""
    records: list[dict[str, Any]] = collected["records"]
    rejects: list[dict[str, Any]] = collected["rejects"]
    total = len(records)

    by_category: dict[str, int] = {}
    by_district: dict[str, int] = {}
    by_dataset: dict[str, int] = {}
    opening = ticket = visit = coords = source = 0
    ru_names = en_names = mixed = unknown = 0
    for r in records:
        cat = str(r.get("category") or "—")
        by_category[cat] = by_category.get(cat, 0) + 1
        dist = str(r.get("district") or "—")
        by_district[dist] = by_district.get(dist, 0) + 1
        by_dataset[r["_dataset"]] = by_dataset.get(r["_dataset"], 0) + 1
        opening += bool((r.get("opening_hours") or "").strip())
        ticket += bool((r.get("ticket_price") or "").strip())
        visit += r.get("visit_minutes") is not None
        source += bool((r.get("source_url") or "").strip())
        coords += r.get("lat") is not None and r.get("lon") is not None
        s = script_of(r.get("name"))
        ru_names += s == "ru"
        en_names += s == "en"
        mixed += s == "mixed"
        unknown += s == "unknown"

    geofence_rejects = [r for r in rejects if r["kind"] == "geofence"]
    invalid_rejects = [r for r in rejects if r["kind"] == "invalid"]
    same_name = find_suspected_duplicates(records, context.duplicate_radius_m)
    duplicates = [p for p in same_name if p["distance_m"] < SAME_OBJECT_RADIUS_M]
    namesakes = [p for p in same_name if p["distance_m"] >= SAME_OBJECT_RADIUS_M]
    generated_labels = count_generated_labels(records)

    report: dict[str, Any] = {
        "generated_at": context.generated_at
        or _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "mode": context.mode,
        "duplicate_radius_m": context.duplicate_radius_m,
        "datasets": collected["datasets"],
        "totals": {
            "records": total,
            "invalid": len(invalid_rejects),
            "geofence_rejects": len(geofence_rejects),
            "valid_in_datasets": sum(d["valid"] for d in collected["datasets"]),
        },
        "by_category": _counts(by_category, total),
        "by_district": _counts(by_district, total),
        "by_dataset": by_dataset,
        "coverage": {
            "source_url": _share(source, total),
            "coordinates": _share(coords, total),
            "opening_hours": _share(opening, total),
            "ticket_price": _share(ticket, total),
            "visit_minutes": _share(visit, total),
        },
        "aliases": {
            "ru_script_names": _share(ru_names, total),
            "en_script_names": _share(en_names, total),
            "mixed_script_names": _share(mixed, total),
            "unknown_script_names": _share(unknown, total),
            "explicit_ru_aliases": _share(len(context.curated or []), total),
            "explicit_en_aliases": _share(0, total),
            "note": (
                "name-script proxy; explicit RU aliases come from the curated CSV, "
                "which holds only the rows that reach a place — the applied set, and the "
                "only one that can produce an alias; EN aliases from place_aliases once a "
                "translation source exists (see db.place_aliases after an apply)"
            ),
        },
        "geofence_rejects": {
            "count": len(geofence_rejects),
            "by_dataset": _count_by(geofence_rejects, "dataset"),
            "samples": geofence_rejects[:10],
        },
        "invalid_rows": {
            "count": len(invalid_rejects),
            "by_dataset": _count_by(invalid_rejects, "dataset"),
            "samples": invalid_rejects[:10],
        },
        "suspected_duplicates": {
            "count": len(duplicates),
            "radius_m": SAME_OBJECT_RADIUS_M,
            "pairs": duplicates[:50],
            "skipped_generated_labels": generated_labels,
            "namesakes": {
                "count": len(namesakes),
                "radius_m": context.duplicate_radius_m,
                "pairs": namesakes[:50],
                "note": (
                    "same name and category, but too far apart to be one object mapped "
                    "twice — reported apart from duplicates so neither number misleads"
                ),
            },
            "note": (
                "same name and category within radius_m; rows whose name is a generated "
                "service label («Остановка», «Туалет») are skipped and counted in "
                "skipped_generated_labels rather than reported as duplicates"
            ),
        },
        "merged": {
            "count": len(context.merged),
            "pairs": context.merged[:50],
            "stale_declarations": context.stale_merges[:20],
            "note": (
                "rule=hand-authored: a protected row shadowed an automatic namesake inside "
                "the duplicate radius · rule=same object: twins collapsed by name and "
                "category · rule=declared: a pair named in place_merges.json. A declaration "
                "with no row behind it is listed in stale_declarations, not ignored"
            ),
        },
        "curated": context.curated_stats or {"rows": len(context.curated or []), "applied": False},
    }
    if context.db_stats is not None:
        report["db"] = context.db_stats
    if context.data_version is not None:
        report["data_version"] = context.data_version
    return report
