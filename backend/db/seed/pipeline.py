"""The database-writing primitives of the seed — the one place rows are written.
Every dataset upserts through :func:`upsert_sql`, so the curated guard holds.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from core.paths import (
    GEO_DIR,
    PHOTOS_DIR,
    PLACE_ALIASES_JSON,
    PLACE_CATEGORIES_JSON,
    PLACE_STREETS_JSON,
)
from core.prices import free_entry
from db.store.datasets import DATA_VERSION_ROW

from .datasets import SOURCE_AUTO, SOURCE_CURATED, Dataset, match_place, read_place_alias_entries

PROVIDER_BY_PREFIX = {
    "city": "planetabelarus",
    "region": "planetabelarus",
    "osm": "openstreetmap",
    "osm_poi": "openstreetmap",
}


def stale_embedding(
    name_expr: LiteralString, category_expr: LiteralString, town_expr: LiteralString
) -> LiteralString:
    """The SQL that drops a row's vector when the text it was built from changes.

    A vector is a projection of ``embeddings.embedded_text`` — the row's name, category
    and town — so every writer of one of those three clears it in the same statement.
    ``embed_missing`` only ever fills ``NULL``, so clearing is enough.
    """
    return (
        f"CASE WHEN places.name IS DISTINCT FROM {name_expr} "
        f"OR places.category IS DISTINCT FROM {category_expr} "
        f"OR places.town IS DISTINCT FROM {town_expr} "
        "THEN NULL ELSE places.embedding END"
    )


def upsert_sql() -> LiteralString:
    """INSERT ... ON CONFLICT (source_url) DO UPDATE that respects curation."""
    protected = "places.category_source IN ('curated', 'dataset')"
    guarded_cat = (
        f"CASE WHEN {protected} AND EXCLUDED.category_source = 'auto' "
        "THEN places.category ELSE EXCLUDED.category END"
    )
    guarded_src = (
        f"CASE WHEN {protected} AND EXCLUDED.category_source = 'auto' "
        "THEN places.category_source ELSE EXCLUDED.category_source END"
    )
    stale = stale_embedding("EXCLUDED.name", guarded_cat, "EXCLUDED.town")
    return f"""
        INSERT INTO places (name, category, category_source, district, town, lat, lon,
                            blurb, fun_fact, fun_facts, opening_hours, ticket_price,
                            is_free, visit_minutes, links, source_url)
        VALUES (%(name)s, %(category)s, %(_category_source)s, %(district)s, %(town)s,
                %(lat)s, %(lon)s, %(blurb)s, %(fun_fact)s, %(fun_facts)s,
                %(opening_hours)s, %(ticket_price)s, %(is_free)s, %(visit_minutes)s,
                %(links)s, %(source_url)s)
        ON CONFLICT (source_url) DO UPDATE SET
            name = EXCLUDED.name,
            category = {guarded_cat},
            category_source = {guarded_src},
            district = EXCLUDED.district, town = EXCLUDED.town,
            lat = EXCLUDED.lat, lon = EXCLUDED.lon, blurb = EXCLUDED.blurb,
            fun_fact = EXCLUDED.fun_fact, fun_facts = EXCLUDED.fun_facts,
            opening_hours = EXCLUDED.opening_hours,
            ticket_price = EXCLUDED.ticket_price,
            is_free = EXCLUDED.is_free,
            visit_minutes = EXCLUDED.visit_minutes, links = EXCLUDED.links,
            embedding = {stale}
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
            r["is_free"] = free_entry(r.get("ticket_price"))
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


def reset_curated(conn: psycopg.Connection, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Give every curated row back to its dataset, so the overlay is applied afresh.

    The CSV is applied by name on every seed, so a row an earlier run marked and this
    one does not choose would otherwise stay marked for good — and that flag is
    protection, which means a wrong one is never rewritten again. A curated row no
    dataset of this run holds is left alone and counted: the seed does not unset what it
    cannot account for. The caller commits.
    """
    known = {str(r["source_url"]): r["_category_source"] for r in records}
    reset = 0
    unknown = 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, source_url FROM places WHERE category_source = %s", (SOURCE_CURATED,)
        )
        for place_id, source_url in cur.fetchall() or ():
            source = known.get(str(source_url))
            if source is None:
                unknown += 1
                continue
            cur.execute("UPDATE places SET category_source = %s WHERE id = %s", (source, place_id))
            reset += 1
    return {"reset": reset, "unknown": unknown}


def apply_curated_rows(conn: psycopg.Connection, curated: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the curated CSV by name and mark its rows as curated.
    Sets ``category_source='curated'`` so later auto-classification cannot rewrite them.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, category, blurb, fun_fact, fun_facts, links, lat, lon, "
            "       category_source FROM places"
        )
        cols = [d.name for d in cur.description or ()]
        db_rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall() or []]

        matched = updated = 0
        unmatched: list[str] = []
        ambiguous: list[str] = []
        for row in curated:
            current, status = match_place(row["name"], db_rows)
            if current is None:
                if status == "ambiguous":
                    ambiguous.append(row["name"])
                else:
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
                       links = %(links)s, category_source = 'curated',
                       embedding = """
                + stale_embedding("%(name)s", "%(category)s", "places.town")
                + """
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
            "ambiguous": ambiguous[:50],
            "ambiguous_count": len(ambiguous),
        }


def resolve_alias_place(
    source_url: str,
    by_source_url: Mapping[str, int],
    by_identity: Mapping[str, int],
) -> int | None:
    """The place an alias's ``source_url`` names: the row itself, or the survivor that
    took its identity in ``place_sources`` when a hand-authored row shadowed it.
    """
    if source_url in by_source_url:
        return by_source_url[source_url]
    src = source_fields(source_url)
    return by_identity.get(f"{src['provider']}/{src['external_id']}")


def _alias_lookups(cur: Any) -> tuple[dict[str, int], dict[str, int]]:
    """``(place by source_url, place by provider/external_id)`` for the alias stage."""
    cur.execute("SELECT source_url, id FROM places")
    by_source_url = {str(url): int(pid) for url, pid in cur.fetchall()}
    cur.execute("SELECT provider, external_id, place_id FROM place_sources")
    by_identity = {
        f"{provider}/{external_id}": int(pid) for provider, external_id, pid in cur.fetchall()
    }
    return by_source_url, by_identity


def apply_place_aliases(conn: psycopg.Connection, path: Path | None = None) -> dict[str, Any]:
    """Write one alias file's declared names into ``place_aliases``.

    The file is the whole truth for the rows it writes: an alias it no longer names is
    deleted, so editing the file is enough. A file that is absent is not a declaration
    and leaves the table alone. The delete is scoped by the file's own name, so the
    English sidecar and the hand-authored file cannot sweep each other's rows. A row the
    seed shadowed is gone from ``places`` but its identity survives in ``place_sources``,
    so the alias lands on the survivor; an alias neither finds is reported, since the file
    claiming a place the seed never wrote is a fact worth seeing. The caller commits.
    """
    source = path or PLACE_ALIASES_JSON
    if not source.is_file():
        return {"skipped": f"no {source.name}"}
    entries = read_place_alias_entries(source)
    label = source.name
    unresolved: list[str] = []
    written = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM place_aliases WHERE source = %s", (label,))
        by_source_url, by_identity = _alias_lookups(cur)
        for entry in entries:
            place_id = resolve_alias_place(entry["source_url"], by_source_url, by_identity)
            if place_id is None:
                unresolved.append(entry["source_url"])
                continue
            cur.execute(
                """
                INSERT INTO place_aliases (place_id, alias, locale, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (place_id, lower(alias), locale) DO NOTHING
                """,
                (place_id, entry["alias"], entry["locale"], label),
            )
            written += cur.rowcount
    return {"written": written, "declared": len(entries), "unresolved": sorted(set(unresolved))}


def load_areas(conn: psycopg.Connection, geo_dir: Path | None = None) -> dict[str, int]:
    """Idempotently write the named-area registry from `areas.json`.

    The polygon follows the row: only an area whose `geometry` names a border file
    gets that ring, and the others keep `geom = NULL`. The file is the whole truth,
    so an area whose geometry it no longer declares has any earlier ring cleared, and
    an area it does not declare at all is deleted — a name resolving to a row no file
    holds would hand the planner a code the data disowns. The caller commits.
    """
    directory = geo_dir or GEO_DIR
    document = json.loads((directory / "areas.json").read_text(encoding="utf-8"))
    declared = [area["slug"] for area in document["areas"]]

    written = 0
    with conn.cursor() as cur:
        for area in document["areas"]:
            slug = area["slug"]
            aliases = [
                term for locale in ("ru", "en") for term in area.get("aliases", {}).get(locale, [])
            ]
            geometry, source, license_ = _area_geometry(directory, area)
            cur.execute(
                """
                INSERT INTO areas (code, name_ru, name_en, aliases, kind, source,
                                   license, note, is_project_area, geom)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s::text IS NULL THEN NULL ELSE ST_GeogFromText(%s) END)
                ON CONFLICT (code) DO UPDATE
                    SET name_ru = EXCLUDED.name_ru, name_en = EXCLUDED.name_en,
                        aliases = EXCLUDED.aliases, kind = EXCLUDED.kind,
                        source = EXCLUDED.source, license = EXCLUDED.license,
                        note = EXCLUDED.note, is_project_area = EXCLUDED.is_project_area,
                        geom = EXCLUDED.geom
                """,
                (
                    slug,
                    area.get("name_ru") or slug,
                    area.get("name_en"),
                    aliases,
                    area.get("kind") or "district",
                    source,
                    license_,
                    area.get("geometry_note"),
                    bool(area.get("is_project_area")),
                    geometry,
                    geometry,
                ),
            )
            written += 1
        cur.execute("DELETE FROM areas WHERE code <> ALL(%s)", (declared,))
        removed = cur.rowcount
    return {"written": written, "removed": removed}


def _area_geometry(
    directory: Path, area: dict[str, Any]
) -> tuple[str | None, str | None, str | None]:
    """(wkt, source, license) from the border file this area's `geometry` names."""
    name = (area.get("geometry") or {}).get("source")
    path = directory / name if name else None
    if path is None or not path.is_file():
        return None, None, None
    meta = json.loads(path.read_text(encoding="utf-8"))
    wkt = _border_wkt(meta)
    if wkt is None:
        return None, None, None
    return wkt, meta.get("source"), meta.get("license")


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
    """Idempotently write the taxonomy. `code` is the natural key.

    Every projected column is written on conflict too, `highlight` included: what the
    map shows beside a route by default is a decision in the file, not one made once by
    a migration, and a flag the file changes has to reach the database on the next seed.
    """
    written = 0
    with conn.cursor() as cur:
        for cat in categories:
            cur.execute(
                """
                INSERT INTO categories (code, ru, en, role, osm_tags, visit_minutes,
                                        aliases_ru, aliases_en, highlight)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                    SET ru = EXCLUDED.ru, en = EXCLUDED.en, role = EXCLUDED.role,
                        osm_tags = EXCLUDED.osm_tags, visit_minutes = EXCLUDED.visit_minutes,
                        aliases_ru = EXCLUDED.aliases_ru, aliases_en = EXCLUDED.aliases_en,
                        highlight = EXCLUDED.highlight
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
                    bool(cat.highlight),
                ),
            )
            written += 1
    return written


def load_itineraries(conn: psycopg.Connection, itineraries: Iterable[Any]) -> int:
    """Idempotently write the curated walks and their named stops.

    The file is the registry: a walk it no longer carries is deleted, and its stops
    with it (the foreign key cascades), so a dropped route cannot stay in the product.
    """
    items = list(itineraries)
    written = 0
    with conn.cursor() as cur:
        for position, item in enumerate(items):
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
        if items:
            cur.execute(
                "DELETE FROM itineraries WHERE id <> ALL(%s)", ([item["id"] for item in items],)
            )
    return written


def _scalar(cur: Any) -> int:
    """The single value the cursor holds, or 0 when it holds nothing."""
    row = cur.fetchone()
    return int(row[0]) if row else 0


def record_data_version(conn: psycopg.Connection, version: str) -> None:
    """Stamp the projection's content version, one singleton row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_versions (name, version) VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET
                version = EXCLUDED.version,
                updated_at = CASE
                    WHEN data_versions.version IS DISTINCT FROM EXCLUDED.version
                    THEN now() ELSE data_versions.updated_at END
            """,
            (DATA_VERSION_ROW, version),
        )


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
    from .photos import apply_to_db

    return apply_to_db(conn, json.loads(path.read_text(encoding="utf-8")))


def apply_place_streets(conn: psycopg.Connection, path: Path | None = None) -> dict[str, Any]:
    """Overlay the committed ``place_streets.json`` on the generated service labels.

    The dataset upsert writes «Остановка» back from the CSV on every seed, so the street
    hint must be laid on *after* it or the next seed would win the name back. The update
    fires only where the row still carries the label the file extends, so a row the data
    has since given a real name is left alone, and a re-run changes nothing. A file that
    is absent is not a declaration and leaves the names as the CSVs wrote them. The
    caller commits.
    """
    from .streets import read_place_street_entries

    source = path or PLACE_STREETS_JSON
    if not source.is_file():
        return {"skipped": f"no {source.name}"}
    entries = read_place_street_entries(source)
    written = 0
    with conn.cursor() as cur:
        for entry in entries:
            cur.execute(
                "UPDATE places SET name = %(named)s, embedding = "
                + stale_embedding("%(named)s", "places.category", "places.town")
                + " WHERE source_url = %(url)s AND name = %(label)s",
                {
                    "named": f"{entry['name']} ({entry['street']})",
                    "url": entry["source_url"],
                    "label": entry["name"],
                },
            )
            written += cur.rowcount
    return {"written": written, "declared": len(entries)}


def apply_place_categories(conn: psycopg.Connection, path: Path | None = None) -> dict[str, Any]:
    """Overlay the committed ``place_categories.json`` on the OSM rows' categories.

    The dataset upsert writes the snapshot's ``historic=ruins`` category back on every
    seed, so the re-derived category must be laid on *after* it or the next seed would
    win it back. Only an automatic row is re-labelled — a curated or dataset category is
    a human's and the trigger would revert the write. The category is part of the text a
    vector is a projection of, so this write clears the vector like any other. A file
    that is absent is not a declaration. The caller commits.
    """
    from .finds import read_place_category_entries

    source = path or PLACE_CATEGORIES_JSON
    if not source.is_file():
        return {"skipped": f"no {source.name}"}
    entries = read_place_category_entries(source)
    written = 0
    with conn.cursor() as cur:
        for entry in entries:
            cur.execute(
                "UPDATE places SET category = %(category)s, "
                "visit_minutes = coalesce(%(visit)s, visit_minutes), embedding = "
                + stale_embedding("places.name", "%(category)s", "places.town")
                + " WHERE source_url = %(url)s AND category_source = 'auto'",
                {
                    "category": entry["category"],
                    "visit": entry["visit_minutes"],
                    "url": entry["source_url"],
                },
            )
            written += cur.rowcount
    return {"written": written, "declared": len(entries)}


def apply_shadowed(
    conn: psycopg.Connection, shadowed: list[dict[str, Any]], licenses: dict[str, str]
) -> int:
    """Drop the automatic rows a hand-authored one shadows, keeping their source on the survivor.
    Returns how many places were removed. The caller commits.
    """
    if not shadowed:
        return 0
    with conn.cursor() as cur:
        losers = [entry["source_url"] for entry in shadowed]
        cur.execute("DELETE FROM places WHERE source_url = ANY(%s)", (losers,))
        removed = cur.rowcount
        for entry in shadowed:
            license_ = licenses.get(entry["dataset"])
            src = source_fields(entry["source_url"], license_)
            cur.execute(
                """
                INSERT INTO place_sources (place_id, provider, external_id, url,
                                          license, fetched_at)
                SELECT id, %s, %s, %s, %s, now() FROM places WHERE source_url = %s
                ON CONFLICT (provider, external_id) DO UPDATE
                    SET place_id = EXCLUDED.place_id
                """,
                (src["provider"], src["external_id"], src["url"], license_, entry["shadowed_by"]),
            )
    return removed


def prune_stale(
    conn: psycopg.Connection, current_urls: Iterable[str], *, apply: bool = False
) -> int:
    """Count (delete with ``apply``) the automatic rows no dataset carries any more."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.source_url FROM places p
            WHERE p.category_source = %s
              AND p.source_url <> ALL(%s)
              AND NOT EXISTS (SELECT 1 FROM visited_places v WHERE v.place_id = p.id)
              AND NOT EXISTS (
                    SELECT 1 FROM itinerary_stops s WHERE s.source_url = p.source_url)
            """,
            (SOURCE_AUTO, list(current_urls)),
        )
        stale = [r[0] for r in cur.fetchall() or []]
        if apply and stale:
            cur.execute("DELETE FROM places WHERE source_url = ANY(%s)", (stale,))
    return len(stale)
