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
from core.paths import PLACES_DIR

from . import taxonomy
from .geofence import inside_project_area

SOURCE_CURATED = "curated"
SOURCE_DATASET = "dataset"
SOURCE_AUTO = "auto"
PROTECTED_CATEGORY_SOURCES = (SOURCE_CURATED, SOURCE_DATASET)

GEOFENCE_PROBLEM = "outside Grodno region"

DEFAULT_DUP_RADIUS_M = _constants.DUPLICATE_RADIUS_M

TWIN_RADIUS_M = 5.0

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

BBOX = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 27.00}

SIGHT_TAXONOMY = frozenset(c.code for c in taxonomy.all_categories() if c.role == "sight")
SERVICE_TAXONOMY = frozenset(c.code for c in taxonomy.all_categories() if c.role == "service")


MIN_BUCKET_ROWS = 2


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


def normalize_city_region(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=True)


def normalize_osm_sight(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=True)


def normalize_osm_service(row: dict[str, Any]) -> dict[str, Any]:
    return _normalize_common(row, visit_required=False)


def _read_pipe(path: Path) -> list[dict[str, Any]]:
    return read_pipe_csv(path)


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
    """The four CSV datasets, in apply order (hand-authored, then OSM)."""
    return [
        Dataset(
            "city",
            filename="places_grodno_city.csv",
            prefix="city:",
            reader=_read_pipe,
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
            reader=_read_pipe,
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
            reader=_read_pipe,
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
            reader=_read_pipe,
            validator=validate_osm_service,
            normalizer=normalize_osm_service,
            category_source=SOURCE_AUTO,
            fatal_invalid=False,
            optional=True,
            license="ODbL 1.0 (OpenStreetMap contributors)",
        ),
    ]


EXPECTED_CURATED_HEADER = [
    "id",
    "normalized_name",
    "category",
    "blurb",
    "fun_fact",
    "fun_facts",
    "links",
]


def read_curated(path: Path) -> list[dict[str, Any]]:
    """Parse the pipe-delimited curated file.
    The header is commented out in the repo, hence the explicit fieldnames.
    """
    if not path.exists():
        return []
    lines = [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    has_header = bool(lines) and not lines[0].split("|", 1)[0].strip().isdigit()
    if has_header and lines[0].split("|")[:4] != EXPECTED_CURATED_HEADER[:4]:
        raise SystemExit(
            f"unexpected header {lines[0].split('|')}, expected {EXPECTED_CURATED_HEADER}"
        )

    reader = csv.DictReader(
        lines, fieldnames=None if has_header else EXPECTED_CURATED_HEADER, delimiter="|"
    )
    return [
        {
            "id": int(row["id"]),
            "name": row["normalized_name"].strip(),
            "category": row["category"].strip() or None,
            "blurb": row["blurb"].strip() or None,
            "fun_fact": (row.get("fun_fact") or "").strip() or None,
            "fun_facts": (row.get("fun_facts") or "").strip() or None,
            "links": (row.get("links") or "").strip() or None,
        }
        for row in reader
    ]


def match_place(name: str, db_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the DB row for a curated name.
    Match on equality first, then case-insensitive containment (not by id).
    """
    n = name.lower()
    exact = [r for r in db_rows if r["name"].strip().lower() == n]
    if exact:
        return exact[0]
    contains = [r for r in db_rows if n in r["name"].lower()]
    if len(contains) == 1:
        return contains[0]
    stripped = n.replace("бывший", "").replace("в гродно", "").strip()
    fuzzy = [r for r in db_rows if stripped in r["name"].lower()]
    if fuzzy:
        return fuzzy[0]
    return None


def load_dataset(ds: Dataset) -> list[dict[str, Any]]:
    """Read one dataset's raw pipe rows (empty for a missing optional dataset)."""
    if not ds.path.exists():
        if ds.optional:
            return []
        raise FileNotFoundError(f"{ds.name}: dataset not found: {ds.path}")
    return ds.reader(ds.path)


def validate_record(ds: Dataset, raw: dict[str, Any]) -> list[str]:
    """Return the problems for one raw row (empty = valid)."""
    return ds.validator(raw)


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
            problems = validate_record(ds, raw)
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
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def find_suspected_duplicates(
    records: list[dict[str, Any]], radius_m: float = DEFAULT_DUP_RADIUS_M
) -> list[dict[str, Any]]:
    """Record pairs with the same normalised name within ``radius_m``.
    Candidates for merging, not proof — reported for review, not dropped.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        key = norm_name(r.get("name"))
        if not key or r.get("lat") is None or r.get("lon") is None:
            continue
        buckets.setdefault(key, []).append(r)

    pairs: list[dict[str, Any]] = []
    for key, rows in buckets.items():
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
                            "distance_m": round(d, 1),
                            "a": {"dataset": a["_dataset"], "source_url": a["source_url"]},
                            "b": {"dataset": b["_dataset"], "source_url": b["source_url"]},
                        }
                    )
    return pairs


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


def resolve_shadowed(
    records: list[dict[str, Any]], radius_m: float = DEFAULT_DUP_RADIUS_M
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split records into the ones to project and the automatic ones a hand-authored row shadows.
    Same normalised name within ``radius_m``; the hand-authored row keeps the place.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r.get("_category_source") not in PROTECTED_CATEGORY_SOURCES:
            continue
        key = norm_name(r.get("name"))
        if key and r.get("lat") is not None and r.get("lon") is not None:
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
    """Collapse automatic records mapping one object twice: same name and category, metres apart.
    Distance is what tells a twin from a namesake — same-name fortifications a hundred
    metres apart are distinct objects, and are left alone.
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
    duplicates = find_suspected_duplicates(records, context.duplicate_radius_m)

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
                "EN aliases from place_aliases once a translation source exists "
                "(see db.place_aliases after an apply)"
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
            "pairs": duplicates[:50],
        },
        "merged": {
            "count": len(context.merged),
            "pairs": context.merged[:50],
        },
        "curated": context.curated_stats or {"rows": len(context.curated or []), "applied": False},
    }
    if context.db_stats is not None:
        report["db"] = context.db_stats
    if context.data_version is not None:
        report["data_version"] = context.data_version
    return report
