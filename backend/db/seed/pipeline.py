"""The database-writing primitives of the seed — the one place rows are written.
Every dataset upserts through :func:`upsert_sql`, so the curated guard holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.paths import GEO_DIR, PHOTOS_DIR
from reference.geofence import inside_project_area

from .datasets import (
    PROTECTED_CATEGORY_SOURCES,
    SOURCE_AUTO,
    Dataset,
    match_place,
)

PROVIDER_BY_PREFIX = {
    "city": "planetabelarus",
    "region": "planetabelarus",
    "osm": "openstreetmap",
    "osm_poi": "openstreetmap",
}


def curated_category_is_protected(category_source: str | None) -> bool:
    """True when automatic classification must not touch this row's category."""
    return (category_source or SOURCE_AUTO) in PROTECTED_CATEGORY_SOURCES


def upsert_sql() -> str:
    """INSERT ... ON CONFLICT (source_url) DO UPDATE that respects curation.
    A protected row keeps its category against automatic writers.
    """
    protected = "places.category_source IN ('curated', 'dataset')"
    guarded_cat = (
        f"CASE WHEN {protected} AND EXCLUDED.category_source = 'auto' "
        "THEN places.category ELSE EXCLUDED.category END"
    )
    guarded_src = (
        f"CASE WHEN {protected} AND EXCLUDED.category_source = 'auto' "
        "THEN places.category_source ELSE EXCLUDED.category_source END"
    )
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
    return {"provider": provider, "external_id": external_id, "url": url, "license": license}


def connect(dsn: str):
    """Open a psycopg connection (imported lazily so --dry-run needs no driver)."""
    from db.connection import connect as db_connect

    return db_connect(dsn, timeout=None)


def allow_curated_category_change(conn) -> None:
    """Opt this transaction in to the guard trigger.
    ``seed`` is the sanctioned writer and enforces curated priority in its upsert.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('grodno.allow_curated_category_change', 'on', true)")


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
                INSERT INTO place_sources (place_id, provider, external_id, url,
                                          license, fetched_at)
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
    Sets ``category_source='curated'`` so later auto-classification cannot rewrite them.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, category, blurb, fun_fact, fun_facts, links FROM places")
        cols = [d.name for d in cur.description]
        db_rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        matched = updated = 0
        unmatched: list[str] = []
        for row in curated:
            current = match_place(row["name"], db_rows)
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
            cur.execute(
                """
                INSERT INTO place_aliases (place_id, alias, locale, source)
                VALUES (%s, %s, 'ru', 'places_curated.csv')
                ON CONFLICT (place_id, lower(alias), locale) DO NOTHING
                """,
                (current["id"], row["name"]),
            )
        return {
            "rows": len(curated),
            "matched": matched,
            "updated": updated,
            "unmatched": unmatched[:50],
            "unmatched_count": len(unmatched),
        }


def load_areas(conn, records: list[dict], geo_dir: Path | None = None) -> int:
    """Idempotently load the project-area polygon + one row per district."""
    written = 0
    with conn.cursor() as cur:
        border = (geo_dir or GEO_DIR) / "grodno_border.json"
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
        cur.execute("SELECT count(*) FROM places WHERE photo_url IS NOT NULL")
        stats["with_photo"] = cur.fetchone()[0]
    return stats


def apply_photos(conn, photos_dir: Path | None = None) -> int:
    """Write the committed ``place_photos.json`` onto ``places`` (no network).
    A missing file yields no photos rather than an error.
    """
    path = (photos_dir or PHOTOS_DIR) / "place_photos.json"
    if not path.exists():
        return 0
    from .photos import apply_to_db

    return apply_to_db(conn, json.loads(path.read_text(encoding="utf-8")))


def prune_foreign(conn, *, apply: bool = False) -> int:
    """Drop places outside the project area; returns the number found (deleted if apply).
    The ingest filters now, so this only cleans older DBs.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, category, district, lat, lon FROM places")
        rows = cur.fetchall()
        foreign = [r for r in rows if not inside_project_area(float(r[4]), float(r[5]))]
        if apply and foreign:
            ids = [r[0] for r in foreign]
            cur.execute("DELETE FROM places WHERE id = ANY(%s)", (ids,))
    return len(foreign)
