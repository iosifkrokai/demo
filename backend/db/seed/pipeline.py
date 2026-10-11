"""The database-writing primitives of the seed — the one place rows are written.
Every dataset upserts through :func:`upsert_sql`, so the curated guard holds.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from core.paths import GEO_DIR, PHOTOS_DIR

from .datasets import (
    PROTECTED_CATEGORY_SOURCES,
    SOURCE_AUTO,
    Dataset,
    match_place,
)
from .geofence import inside_project_area

PROVIDER_BY_PREFIX = {
    "city": "planetabelarus",
    "region": "planetabelarus",
    "osm": "openstreetmap",
    "osm_poi": "openstreetmap",
}


def curated_category_is_protected(category_source: str | None) -> bool:
    """True when automatic classification must not touch this row's category."""
    return (category_source or SOURCE_AUTO) in PROTECTED_CATEGORY_SOURCES


def upsert_sql() -> LiteralString:
    """INSERT ... ON CONFLICT (source_url) DO UPDATE that respects curation.

    A protected row keeps its category against automatic writers. The statement is
    assembled from the column names below and nothing else — no value ever reaches
    it — so it is a literal by construction, which is what the return type says and
    what psycopg's `LiteralString` guard exists to check.
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


def source_fields(source_url: str, license: str | None = None) -> dict[str, Any]:
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


def allow_curated_category_change(conn: psycopg.Connection) -> None:
    """Opt this transaction in to the guard trigger.
    ``seed`` is the sanctioned writer and enforces curated priority in its upsert.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('grodno.allow_curated_category_change', 'on', true)")


def apply_dataset(
    conn: psycopg.Connection, ds: Dataset, records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Upsert one dataset's rows; returns {'inserted', 'updated'}."""
    statement = upsert_sql()
    inserted = updated = 0
    with conn.cursor() as cur:
        for r in records:
            cur.execute("SELECT 1 FROM places WHERE source_url = %s", (r["source_url"],))
            was_new = cur.fetchone() is None
            cur.execute(sql.SQL(statement), r)
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


def apply_curated_rows(conn: psycopg.Connection, curated: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the curated CSV by name and mark its rows as curated.
    Sets ``category_source='curated'`` so later auto-classification cannot rewrite them.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, category, blurb, fun_fact, fun_facts, links FROM places")
        cols = [d.name for d in cur.description or ()]
        db_rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall() or []]

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


def load_areas(conn: psycopg.Connection, geo_dir: Path | None = None) -> int:
    """Idempotently write the named-area registry from `areas.json`.

    The registry file is the source, exactly as `taxonomy.csv` is for the
    categories: every slug, name, alias and note is written here, and the project
    area additionally carries the polygon from `grodno_border.json`. The project
    row is keyed by the slug the file marks `is_project_area` (`grodno-oblast`),
    not a code invented from an English alias.
    """
    directory = geo_dir or GEO_DIR
    document = json.loads((directory / "areas.json").read_text(encoding="utf-8"))
    border = directory / "grodno_border.json"
    border_meta = json.loads(border.read_text(encoding="utf-8")) if border.exists() else {}
    geometry = _border_wkt(border_meta)

    written = 0
    with conn.cursor() as cur:
        for area in document["areas"]:
            slug = area["slug"]
            aliases = [
                term for locale in ("ru", "en") for term in area.get("aliases", {}).get(locale, [])
            ]
            cur.execute(
                """
                INSERT INTO areas (code, name_ru, name_en, aliases, kind, source,
                                   license, note, is_project_area, geom)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s IS NULL THEN NULL ELSE ST_GeogFromText(%s) END)
                ON CONFLICT (code) DO UPDATE
                    SET name_ru = EXCLUDED.name_ru, name_en = EXCLUDED.name_en,
                        aliases = EXCLUDED.aliases, kind = EXCLUDED.kind,
                        source = EXCLUDED.source, license = EXCLUDED.license,
                        note = EXCLUDED.note, is_project_area = EXCLUDED.is_project_area,
                        geom = COALESCE(EXCLUDED.geom, areas.geom)
                """,
                (
                    slug,
                    area.get("name_ru") or slug,
                    area.get("name_en"),
                    aliases,
                    area.get("kind") or "district",
                    border_meta.get("source"),
                    border_meta.get("license"),
                    area.get("geometry_note"),
                    bool(area.get("is_project_area")),
                    geometry,
                    geometry,
                ),
            )
            written += 1
    return written


def _border_wkt(meta: dict[str, Any]) -> str | None:
    """The project border as WKT, or None when the file carries no rings."""
    rings = meta.get("rings") or []
    if not rings:
        return None
    pts = [(float(x), float(y)) for x, y in rings[0]]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return "MULTIPOLYGON(((" + ", ".join(f"{lon} {lat}" for lon, lat in pts) + ")))"


def load_categories(conn: psycopg.Connection, categories: Iterable[Any]) -> int:
    """Idempotently write the taxonomy. `code` is the natural key."""
    written = 0
    with conn.cursor() as cur:
        for cat in categories:
            cur.execute(
                """
                INSERT INTO categories (code, ru, en, role, osm_tags, visit_minutes,
                                        aliases_ru, aliases_en)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                    SET ru = EXCLUDED.ru, en = EXCLUDED.en, role = EXCLUDED.role,
                        osm_tags = EXCLUDED.osm_tags, visit_minutes = EXCLUDED.visit_minutes,
                        aliases_ru = EXCLUDED.aliases_ru, aliases_en = EXCLUDED.aliases_en
                """,
                (
                    cat.code,
                    cat.ru,
                    cat.en,
                    cat.role,
                    list(cat.osm_tags),
                    cat.visit_minutes,
                    list(cat.aliases_ru),
                    list(cat.aliases_en),
                ),
            )
            written += 1
    return written


def load_itineraries(conn: psycopg.Connection, itineraries: Iterable[Any]) -> int:
    """Idempotently write the curated walks and their named stops.

    `id` is the natural key; the stops are rewritten wholesale, because an
    itinerary that loses a stop must lose it in the database too.
    """
    written = 0
    with conn.cursor() as cur:
        for position, item in enumerate(itineraries):
            cur.execute(
                """
                INSERT INTO itineraries (id, title, blurb, transport, position)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                    SET title = EXCLUDED.title, blurb = EXCLUDED.blurb,
                        transport = EXCLUDED.transport, position = EXCLUDED.position
                """,
                (
                    item["id"],
                    item.get("title") or item["id"],
                    item.get("blurb") or "",
                    item.get("transport") or "pedestrian",
                    position,
                ),
            )
            cur.execute("DELETE FROM itinerary_stops WHERE itinerary_id = %s", (item["id"],))
            for ordinal, source_url in enumerate(item.get("stops") or ()):
                cur.execute(
                    "INSERT INTO itinerary_stops (itinerary_id, ordinal, source_url) "
                    "VALUES (%s, %s, %s)",
                    (item["id"], ordinal, source_url),
                )
            written += 1
    return written


def _scalar(cur: Any) -> int:
    """The single value the cursor holds, or 0 when it holds nothing."""
    row = cur.fetchone()
    return int(row[0]) if row else 0


def gather_db_stats(conn: psycopg.Connection) -> dict[str, Any]:
    """Post-apply counts: sources/areas/aliases and pending embeddings."""
    stats: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM places")
        stats["places_total"] = _scalar(cur)
        cur.execute("SELECT count(*) FROM places WHERE embedding IS NULL")
        stats["pending_embeddings"] = _scalar(cur)
        cur.execute("SELECT category_source, count(*) FROM places GROUP BY 1")
        stats["by_category_source"] = {r[0]: r[1] for r in cur.fetchall() or []}
        cur.execute("SELECT provider, count(*) FROM place_sources GROUP BY 1")
        stats["place_sources"] = {r[0]: r[1] for r in cur.fetchall() or []}
        cur.execute("SELECT locale, count(*) FROM place_aliases GROUP BY 1")
        stats["place_aliases"] = {r[0]: r[1] for r in cur.fetchall() or []}
        cur.execute("SELECT count(*) FROM areas")
        stats["areas"] = _scalar(cur)
        cur.execute("SELECT count(*) FROM places WHERE photo_url IS NOT NULL")
        stats["with_photo"] = _scalar(cur)
    return stats


def apply_photos(conn: psycopg.Connection, photos_dir: Path | None = None) -> int:
    """Write the committed ``place_photos.json`` onto ``places`` (no network).
    A missing file yields no photos rather than an error.
    """
    path = (photos_dir or PHOTOS_DIR) / "place_photos.json"
    if not path.exists():
        return 0
    from .photos import apply_to_db  # deferred: photos imports this module

    return apply_to_db(conn, json.loads(path.read_text(encoding="utf-8")))


def prune_foreign(conn: psycopg.Connection, *, apply: bool = False) -> int:
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
