#!/usr/bin/env python3
"""seed_all.py — the single reproducible entry point for the places database.

One command does the whole spec section 6 pipeline over the versioned CSVs:

    validate  →  upsert (idempotent, by natural key)  →  coverage report

Contract
--------
* Data is code: every dataset is a versioned file in ``backend/data/``. The DB
  is a projection, never the only carrier of knowledge (constitution §3).
* Idempotent: upserts are keyed on the natural key ``places.source_url``; a
  second run inserts nothing new and never overwrites a hand-curated category
  (constitution §4, spec §6.6).
* ``--dry-run`` reads only files: no DB connection, no network. Everything the
  report needs is computed in memory from the CSVs.
* Coverage report is machine-readable JSON (``--report PATH``); it is honest:
  unknown stays unknown, and rows rejected by the geofence are quarantined and
  counted instead of being silently published (spec §6.2, §6.6).

Curated-category protection
---------------------------
``places.category_source`` records who owns a row's category:
``curated`` (data/places_curated.csv), ``dataset`` (hand-authored city/region
CSVs) or ``auto`` (OSM tag mapping / automatic classification). seed_all writes
the right marker, the upsert never lets an automatic writer change a
curated/dataset row's category, migration 0004 adds a DB trigger enforcing the
same rule, and ``scripts/enrich_places.py`` only classifies ``auto`` rows.

Usage
-----
    python scripts/seed_all.py --dry-run            # offline: validate + report
    python scripts/seed_all.py --report /tmp/seed.json
    python scripts/seed_all.py                      # apply to DATABASE_URL
    python scripts/seed_all.py --no-embed           # skip embeddings

Exit codes: 0 = ok, 1 = DB/apply error, 2 = fatal validation failure.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent          # backend/scripts
BACKEND = SCRIPT_DIR.parent                            # backend/
DEV_DATA_DIR = BACKEND / "data"

# Reuse the existing loaders/validators verbatim — public names are unchanged.
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPT_DIR))

import apply_curated  # noqa: E402
import load_osm  # noqa: E402
import seed_region  # noqa: E402

from agent import constants as _constants  # noqa: E402

# ── Category ownership markers ───────────────────────────────────────────────
SOURCE_CURATED = "curated"
SOURCE_DATASET = "dataset"
SOURCE_AUTO = "auto"
# Categories in these rows may never be rewritten by automatic classification.
PROTECTED_CATEGORY_SOURCES = (SOURCE_CURATED, SOURCE_DATASET)

# The geofence message emitted by the reused validators; used to split a plain
# validation failure from a geofence rejection in the report.
GEOFENCE_PROBLEM = "outside Grodno region"

# Same thresholds the rest of the pipeline uses (agent/constants.py).
DEFAULT_DUP_RADIUS_M = _constants.DUPLICATE_RADIUS_M

# Provider recorded in place_sources, keyed by the source_url prefix.
PROVIDER_BY_PREFIX = {
    "city": "planetabelarus",
    "region": "planetabelarus",
    "osm": "openstreetmap",
    "osm_poi": "openstreetmap",
}


class Dataset:
    """One versioned CSV dataset with its loader and natural-key prefix."""

    def __init__(
        self,
        name: str,
        *,
        filename: str,
        prefix: str,
        reader: Callable[[Path], list[dict]],
        validator: Callable[[dict], list[str]],
        normalizer: Callable[[dict], dict],
        category_source: str,
        fatal_invalid: bool,
        license: str = "see file header",
    ) -> None:
        self.name = name
        self.filename = filename
        self.prefix = prefix
        self.reader = reader
        self.validator = validator
        self.normalizer = normalizer
        self.category_source = category_source
        self.fatal_invalid = fatal_invalid
        self.license = license
        self.path: Path = DEV_DATA_DIR / filename

    def with_data_dir(self, data_dir: Path) -> Dataset:
        self.path = data_dir / self.filename
        return self


def default_datasets() -> list[Dataset]:
    """The three CSV datasets, in apply order (region/city first, OSM last)."""
    return [
        Dataset("city", filename="places_grodno_city.csv", prefix="city:",
                reader=seed_region.read_rows, validator=seed_region.validate,
                normalizer=seed_region.normalize, category_source=SOURCE_DATASET,
                fatal_invalid=True,
                license="hand-authored (planetabelarus.by derived)"),
        Dataset("region", filename="places_region.csv", prefix="region:",
                reader=seed_region.read_rows, validator=seed_region.validate,
                normalizer=seed_region.normalize, category_source=SOURCE_DATASET,
                fatal_invalid=True,
                license="hand-authored (planetabelarus.by derived)"),
        Dataset("osm", filename="places_osm_raw.csv", prefix="osm:",
                reader=load_osm.read_csv, validator=load_osm.validate,
                normalizer=load_osm.normalize, category_source=SOURCE_AUTO,
                fatal_invalid=False,
                license="ODbL 1.0 (OpenStreetMap contributors)"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Loading + validation (pure, offline)
# ─────────────────────────────────────────────────────────────────────────────

def load_dataset(ds: Dataset) -> list[dict]:
    """Read one dataset's raw pipe rows."""
    if not ds.path.exists():
        raise FileNotFoundError(f"{ds.name}: dataset not found: {ds.path}")
    return ds.reader(ds.path)


def validate_record(ds: Dataset, raw: dict) -> list[str]:
    """Return the problems for one raw row (empty = valid)."""
    return ds.validator(raw)


def classify_reject(problems: list[str]) -> str:
    """'geofence' when the point is outside the project area, else 'invalid'."""
    if any(GEOFENCE_PROBLEM in p for p in problems):
        return "geofence"
    return "invalid"


def collect_records(datasets: list[Dataset]) -> dict[str, Any]:
    """Validate every dataset.

    Returns a dict with ``records`` (valid, normalised, tagged by dataset),
    ``rejects`` (invalid + geofence, kept in quarantine) and ``fatal`` (True when
    a fatal dataset had an invalid row — region/city must never be half-loaded).
    """
    records: list[dict] = []
    rejects: list[dict] = []
    datasets_meta: list[dict] = []
    fatal = False

    for ds in datasets:
        raw_rows = load_dataset(ds)
        valid = 0
        invalid = 0
        geofence = 0
        for raw in raw_rows:
            problems = validate_record(ds, raw)
            if problems:
                kind = classify_reject(problems)
                if kind == "geofence":
                    geofence += 1
                else:
                    invalid += 1
                rejects.append({
                    "dataset": ds.name,
                    "name": str(raw.get("name", "")).strip(),
                    "source_url": str(raw.get("source_url", "")).strip(),
                    "kind": kind,
                    "problems": problems,
                })
                if ds.fatal_invalid:
                    fatal = True
                continue
            row = ds.normalizer(raw)
            row["_dataset"] = ds.name
            row["_category_source"] = ds.category_source
            records.append(row)
            valid += 1
        datasets_meta.append({
            "name": ds.name, "file": ds.filename, "rows": len(raw_rows),
            "valid": valid, "invalid": invalid, "geofence_rejects": geofence,
            "category_source": ds.category_source, "license": ds.license,
        })

    return {"records": records, "rejects": rejects,
            "datasets": datasets_meta, "fatal": fatal}


def read_curated(path: Path) -> list[dict]:
    """Curated rows (id/name/category/...), parsed by the existing loader."""
    if not path.exists():
        return []
    return apply_curated.read_curated(str(path))


# ─────────────────────────────────────────────────────────────────────────────
# Coverage report (pure, offline)
# ─────────────────────────────────────────────────────────────────────────────

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЎў]")  # noqa: RUF001 — Cyrillic ranges are intended
_LATIN_RE = re.compile(r"[A-Za-z]")
_NORM_RE = re.compile(r"[^0-9a-zа-яёіў]+")  # noqa: RUF001 — Cyrillic tail is intended


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
    """Normalised name for duplicate clustering (case/space/punctuation-insensitive)."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name).lower()
    return _NORM_RE.sub(" ", n).strip()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def find_suspected_duplicates(
    records: list[dict], radius_m: float = DEFAULT_DUP_RADIUS_M
) -> list[dict]:
    """Record pairs with the same normalised name within ``radius_m``.

    Same-name-within-radius is a *candidate* for merging, not proof (spec §6.3),
    so these are reported for review rather than dropped.
    """
    buckets: dict[str, list[dict]] = {}
    for r in records:
        key = norm_name(r.get("name"))
        if not key or r.get("lat") is None or r.get("lon") is None:
            continue
        buckets.setdefault(key, []).append(r)

    pairs: list[dict] = []
    for key, rows in buckets.items():
        if len(rows) < 2:
            continue
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                d = _haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if d < radius_m:
                    pairs.append({
                        "name": key,
                        "distance_m": round(d, 1),
                        "a": {"dataset": a["_dataset"], "source_url": a["source_url"]},
                        "b": {"dataset": b["_dataset"], "source_url": b["source_url"]},
                    })
    return pairs


def _share(part: int, total: int) -> dict:
    return {"count": part, "total": total,
            "share": round(part / total, 4) if total else None}


def _counts(values, total: int) -> dict:
    out = {str(k): v for k, v in values.items()}
    out["_total"] = total
    return out


def build_coverage_report(
    collected: dict[str, Any],
    *,
    mode: str,
    curated: list[dict] | None = None,
    curated_stats: dict | None = None,
    duplicate_radius_m: float = DEFAULT_DUP_RADIUS_M,
    db_stats: dict | None = None,
    generated_at: str | None = None,
) -> dict:
    """Assemble the machine-readable coverage report from in-memory records."""
    records: list[dict] = collected["records"]
    rejects: list[dict] = collected["rejects"]
    total = len(records)

    by_category: dict[str, int] = {}
    by_district: dict[str, int] = {}
    by_dataset: dict[str, int] = {}
    opening = ticket = visit = coords = source = 0
    ru_names = en_names = mixed = unknown = 0
    for r in records:
        by_category[str(r.get("category") or "—")] = by_category.get(str(r.get("category") or "—"), 0) + 1
        by_district[str(r.get("district") or "—")] = by_district.get(str(r.get("district") or "—"), 0) + 1
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
    duplicates = find_suspected_duplicates(records, duplicate_radius_m)

    report: dict[str, Any] = {
        "generated_at": generated_at or _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "duplicate_radius_m": duplicate_radius_m,
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
            "explicit_ru_aliases": _share(len(curated or []), total),
            "explicit_en_aliases": _share(0, total),
            "note": ("name-script proxy; explicit RU aliases come from the curated CSV, "
                     "EN aliases from place_aliases once a translation source exists "
                     "(see db.place_aliases after an apply)"),
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
        "curated": curated_stats or {"rows": len(curated or []), "applied": False},
    }
    if db_stats is not None:
        report["db"] = db_stats
    return report


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Curated-category protection helpers
# ─────────────────────────────────────────────────────────────────────────────

def curated_category_is_protected(category_source: str | None) -> bool:
    """True when automatic classification must not touch this row's category."""
    return (category_source or SOURCE_AUTO) in PROTECTED_CATEGORY_SOURCES


def upsert_sql() -> str:
    """INSERT ... ON CONFLICT (source_url) DO UPDATE that respects curation.

    A protected row (curated/dataset) keeps its category against *automatic*
    writers (``EXCLUDED.category_source = 'auto'``), while the row's own
    authoritative dataset can still refresh it — re-running city/region or
    places_curated.csv applies edits to their own rows. An automatic writer can
    therefore never overwrite hand-labelled data, no matter how often the seed runs.
    """
    protected = "places.category_source IN ('curated', 'dataset')"
    guarded_cat = (f"CASE WHEN {protected} AND EXCLUDED.category_source = 'auto' "
                   "THEN places.category ELSE EXCLUDED.category END")
    guarded_src = (f"CASE WHEN {protected} AND EXCLUDED.category_source = 'auto' "
                   "THEN places.category_source ELSE EXCLUDED.category_source END")
    return f"""
        INSERT INTO places (name, category, category_source, district, town, lat, lon,
                            blurb, fun_fact, fun_facts, opening_hours, ticket_price,
                            visit_minutes, links, source_url)
        VALUES (%(name)s, %(category)s, %(_category_source)s, %(district)s, %(town)s,
                %(lat)s, %(lon)s, %(blurb)s, %(fun_fact)s, %(fun_facts)s,
                %(opening_hours)s, %(ticket_price)s, %(visit_minutes)s, %(links)s,
                %(source_url)s)
        ON CONFLICT (source_url) DO UPDATE SET
            name = EXCLUDED.name,
            category = {guarded_cat},
            category_source = {guarded_src},
            district = EXCLUDED.district, town = EXCLUDED.town,
            lat = EXCLUDED.lat, lon = EXCLUDED.lon, blurb = EXCLUDED.blurb,
            fun_fact = EXCLUDED.fun_fact, fun_facts = EXCLUDED.fun_facts,
            opening_hours = EXCLUDED.opening_hours,
            ticket_price = EXCLUDED.ticket_price,
            visit_minutes = EXCLUDED.visit_minutes, links = EXCLUDED.links
    """


def source_fields(source_url: str, license: str | None = None) -> dict:
    """(provider, external_id, url) for place_sources from a source_url key."""
    prefix, _, rest = source_url.partition(":")
    provider = PROVIDER_BY_PREFIX.get(prefix, prefix or "unknown")
    external_id = rest or source_url
    url = source_url if source_url.startswith("http") else None
    return {"provider": provider, "external_id": external_id, "url": url,
            "license": license}


# ─────────────────────────────────────────────────────────────────────────────
# Database apply (imported lazily so --dry-run needs no DB)
# ─────────────────────────────────────────────────────────────────────────────

def _connect(dsn: str):
    import psycopg  # noqa: PLC0415 — only needed when actually writing

    return psycopg.connect(dsn)


def apply_dataset(conn, ds: Dataset, records: list[dict]) -> dict:
    """Upsert one dataset's rows; returns {'inserted', 'updated'}."""
    sql = upsert_sql()
    inserted = updated = 0
    with conn.cursor() as cur:
        for r in records:
            cur.execute("SELECT 1 FROM places WHERE source_url = %s", (r["source_url"],))
            was_new = cur.fetchone() is None
            cur.execute(sql, r)
            src = source_fields(r["source_url"], ds.license)
            cur.execute(
                """
                INSERT INTO place_sources (place_id, provider, external_id, url, license, fetched_at)
                SELECT id, %s, %s, %s, %s, now() FROM places WHERE source_url = %s
                ON CONFLICT (provider, external_id) DO UPDATE
                    SET url = EXCLUDED.url, license = EXCLUDED.license
                """,
                (src["provider"], src["external_id"], src["url"], ds.license, r["source_url"]),
            )
            if was_new:
                inserted += 1
            else:
                updated += 1
    return {"inserted": inserted, "updated": updated}


def apply_curated_rows(conn, curated: list[dict]) -> dict:
    """Apply the curated CSV by name and mark its rows as curated.

    Sets ``category_source='curated'`` so later automatic classification can never
    rewrite those categories, and opts in to the 0004 guard trigger for this
    transaction so a genuine re-curation is still allowed here.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('grodno.allow_curated_category_change', 'on', true)")
        cur.execute("SELECT id, name, category, blurb, fun_fact, fun_facts, links FROM places")
        cols = [d.name for d in cur.description]
        db_rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        matched = updated = 0
        unmatched: list[str] = []
        for row in curated:
            current = apply_curated._match_place(row["name"], db_rows)
            if not current:
                unmatched.append(row["name"])
                continue
            matched += 1
            fields = ("name", "category", "blurb", "fun_fact", "fun_facts", "links")
            differs = any(current[f] != row[f] for f in fields)
            cur.execute(
                """
                UPDATE places
                   SET name = %(name)s, category = %(category)s, blurb = %(blurb)s,
                       fun_fact = %(fun_fact)s, fun_facts = %(fun_facts)s,
                       links = %(links)s, category_source = 'curated'
                 WHERE id = %(id)s
                """,
                dict(row, id=current["id"]),
            )
            if differs:
                updated += 1
            # Curated normalised_name becomes a RU alias for grounding.
            cur.execute(
                """
                INSERT INTO place_aliases (place_id, alias, locale, source)
                VALUES (%s, %s, 'ru', 'places_curated.csv')
                ON CONFLICT (place_id, lower(alias), locale) DO NOTHING
                """,
                (current["id"], row["name"]),
            )
        return {"rows": len(curated), "matched": matched, "updated": updated,
                "unmatched": unmatched[:50], "unmatched_count": len(unmatched)}


def load_areas(conn, records: list[dict], data_dir: Path) -> int:
    """Idempotently load the project-area polygon + one row per district."""
    written = 0
    with conn.cursor() as cur:
        border = data_dir / "grodno_border.json"
        if border.exists():
            meta = json.loads(border.read_text(encoding="utf-8"))
            rings = meta.get("rings", [])
            if rings:
                ring = rings[0]
                pts = [(float(x), float(y)) for x, y in ring]
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                wkt = "MULTIPOLYGON(((" + ", ".join(f"{lon} {lat}" for lon, lat in pts) + ")))"
                cur.execute(
                    """
                    INSERT INTO areas (code, name_ru, name_en, aliases, kind, source, license, geom)
                    VALUES ('grodno-voblast', 'Гродненская область', 'Grodno Region',
                            ARRAY['Гродненская область', 'Grodno'], 'project-area',
                            %s, %s, ST_GeogFromText(%s))
                    ON CONFLICT (code) DO UPDATE
                        SET geom = EXCLUDED.geom, source = EXCLUDED.source,
                            license = EXCLUDED.license
                    """,
                    (meta.get("source"), meta.get("license"), wkt),
                )
                written += 1
        districts = sorted({r["district"] for r in records if r.get("district")})
        for district in districts:
            code = "district:" + re.sub(r"\s+", "-", district.strip().lower())
            cur.execute(
                """
                INSERT INTO areas (code, name_ru, kind, source)
                VALUES (%s, %s, 'district', 'derived:places.district')
                ON CONFLICT (code) DO NOTHING
                """,
                (code, district),
            )
            written += 1
    return written


def gather_db_stats(conn) -> dict:
    """Post-apply counts: sources/areas/aliases and pending embeddings."""
    stats: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM places")
        stats["places_total"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM places WHERE embedding IS NULL")
        stats["pending_embeddings"] = cur.fetchone()[0]
        cur.execute("SELECT category_source, count(*) FROM places GROUP BY 1")
        stats["by_category_source"] = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT provider, count(*) FROM place_sources GROUP BY 1")
        stats["place_sources"] = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT locale, count(*) FROM place_aliases GROUP BY 1")
        stats["place_aliases"] = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT count(*) FROM areas")
        stats["areas"] = cur.fetchone()[0]
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Human summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(report: dict) -> None:
    t = report["totals"]
    print(f"[seed_all] mode={report['mode']} records={t['records']} "
          f"geofence_rejects={t['geofence_rejects']} invalid={t['invalid']} "
          f"duplicates={report['suspected_duplicates']['count']}")
    for ds in report["datasets"]:
        print(f"    {ds['name']:>6}: {ds['valid']:>5} valid / {ds['rows']:>5} rows "
              f"(geofence {ds['geofence_rejects']}, invalid {ds['invalid']}, "
              f"category_source={ds['category_source']})")
    cov = report["coverage"]
    print("  coverage: "
          + ", ".join(f"{k}={v['share']}" for k, v in cov.items()))
    print(f"  by_category: {report['by_category']}")
    print(f"  by_district: {report['by_district']}")
    if "db" in report:
        print(f"  db: {report['db']}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seed_all.py",
        description="Reproducible seed: validate → upsert → coverage report.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate + report only. No DB, no network.")
    p.add_argument("--data-dir", type=Path, default=DEV_DATA_DIR,
                   help="Directory holding the CSV datasets (default: backend/data).")
    p.add_argument("--report", type=Path, default=None,
                   help="Write the machine-readable JSON report to this path.")
    p.add_argument("--json", action="store_true",
                   help="Also print the JSON report to stdout.")
    p.add_argument("--no-embed", action="store_true",
                   help="Skip embeddings (seed still works without a key).")
    p.add_argument("--no-areas", action="store_true",
                   help="Do not (re)load areas/place_sources.")
    p.add_argument("--database-url", default=None,
                   help="Override DATABASE_URL for the apply step.")
    return p


def run(args: argparse.Namespace) -> int:
    data_dir: Path = args.data_dir
    datasets = [ds.with_data_dir(data_dir) for ds in default_datasets()]
    curated_path = data_dir / "places_curated.csv"

    # 1. validate (offline, always)
    collected = collect_records(datasets)
    curated = read_curated(curated_path)
    curated_stats = {
        "rows": len(curated),
        "applied": False,
        "by_category": _count_by(curated, "category") if curated else {},
    }

    # 2. upsert (skipped entirely on --dry-run)
    db_stats: dict | None = None
    if not args.dry_run:
        dsn = args.database_url or os.environ.get(
            "DATABASE_URL", "postgresql://grodno:***@localhost:5432/grodno")
        try:
            with _connect(dsn) as conn:
                # seed_all is the sanctioned writer: it opts in to the 0004 guard
                # trigger for this transaction and enforces curated priority in its
                # own upsert SQL instead (see upsert_sql / apply_curated_rows).
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('grodno.allow_curated_category_change', 'on', true)")
                totals = {"inserted": 0, "updated": 0}
                for ds in datasets:
                    rows = [r for r in collected["records"] if r["_dataset"] == ds.name]
                    out = apply_dataset(conn, ds, rows)
                    totals["inserted"] += out["inserted"]
                    totals["updated"] += out["updated"]
                if curated:
                    curated_stats.update(apply_curated_rows(conn, curated))
                    curated_stats["applied"] = True
                areas_written = 0
                if not args.no_areas:
                    areas_written = load_areas(conn, collected["records"], data_dir)
                conn.commit()

                want_embed = (not args.no_embed and bool(os.environ.get("OPENROUTER_API_KEY")))
                embedded = 0
                if want_embed:
                    with conn.cursor() as cur:
                        embedded = seed_region.embed_missing(cur)
                    embedded += load_osm.embed_missing(conn)
                    conn.commit()
                db_stats = gather_db_stats(conn)
                db_stats.update({"upserted": totals, "areas_written": areas_written,
                                 "embedded": embedded,
                                 "embeddings_skipped": not want_embed})
        except Exception as exc:
            sys.stderr.write(f"[seed_all] apply failed: {exc}\n")
            return 1

    # 3. report (always)
    report = build_coverage_report(collected, mode="dry-run" if args.dry_run else "apply",
                                   curated=curated, curated_stats=curated_stats,
                                   db_stats=db_stats)
    print_summary(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[seed_all] report written to {args.report}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if collected["fatal"]:
        sys.stderr.write("[seed_all] fatal: a hand-authored dataset (city/region) has "
                         "invalid rows — fix data/*.csv before publishing\n")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
