"""Bounded tool surface for the interpretation agent."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from core.config import DSN
from domain.taxonomy import db_values

log = logging.getLogger(__name__)

MAX_PLACES_PER_SEARCH = 8
MAX_FACTS_PER_CALL = 1
MAX_LINKS_PER_PLACE = 3
MAX_AREAS_PER_CALL = 8
MAX_SERVICES_PER_CALL = 5
MAX_ROUTE_POINTS = 64
MAX_DETOUR_MINUTES = 30
MIN_RADIUS_M = 100.0
MAX_RADIUS_M = 20_000.0
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_RADIUS_M = 1_000.0

ERR_EMPTY_QUERY = "empty_query"
ERR_BAD_ARGUMENT = "bad_argument"
ERR_NOT_FOUND = "not_found"
ERR_DB_UNAVAILABLE = "db_unavailable"
ERR_AREA_REGISTRY = "area_registry_unavailable"
ERR_NOT_IMPLEMENTED = "not_implemented"

DB_TIMEOUT_S = 3.0

_PLACE_FIELDS = ("id", "name", "category", "town", "district", "lat", "lon")
_FACT_FIELDS = (
    "name",
    "category",
    "town",
    "district",
    "opening_hours",
    "ticket_price",
    "visit_minutes",
    "source_url",
)
_AREA_FIELDS = ("code", "name_ru", "name_en", "kind")


def _envelope(
    tool: str,
    results: list[dict],
    provenance: dict,
    *,
    capped: bool = False,
    error: str | None = None,
    message: str | None = None,
) -> dict:
    """One shape for every tool answer: data + provenance + honest statuses."""
    return {
        "results": results,
        "count": len(results),
        "capped": capped,
        "error": error,
        "message": message,
        "provenance": {"tool": tool, **provenance},
    }


def _clamp(
    value: Any, low: int | float, high: int | float, default: int | float
) -> tuple[int | float, bool]:
    """Clamp a number the model sent into [low, high]; report whether we did.

    A missing/unparseable value becomes `default`, not an error.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default, False
    if value < low:
        return low, value != low
    if value > high:
        return high, True
    return value, False


def _project(row: dict, fields: tuple[str, ...]) -> dict:
    return {f: row.get(f) for f in fields if f in row}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _connect() -> Any:
    """Open a short-lived psycopg connection."""
    import psycopg

    return psycopg.connect(DSN, connect_timeout=int(DB_TIMEOUT_S))


def _db_search_rows(
    db: Any,
    *,
    query: str,
    limit: int,
    near: tuple[float, float, float] | None,
) -> list[dict]:
    """Rows from the shared retrieval layer — no SQL is ever model-supplied.

    Reuses the same signals the deterministic pipeline uses.
    """
    from store import search as search_mod

    if near is not None:
        lat, lon, radius_m = near
        return search_mod.nearby_places(db, lat, lon, radius_km=radius_m / 1000.0, limit=limit)
    return search_mod._keyword_search(db, query, limit=limit)


def _db_place_row(db: Any, place_id: int) -> dict | None:
    """One place row by id, or None."""
    from store import search as search_mod

    rows = search_mod.fetch_points_by_ids(db, [place_id])
    return rows[0] if rows else None


def _norm_term(text: str) -> str:
    """Lowercase, ё-folded, whitespace-collapsed — for term matching only."""
    return " ".join(str(text).lower().replace("ё", "е").split())


def _areas_from_registry(term: str, locale: str, limit: int) -> list[dict]:
    """Canonical area slugs from the versioned registry (``domain/areas.py``)."""
    from domain.areas import load_areas, resolve_area

    registry = load_areas()
    found: list[str] = []
    slug = resolve_area(term, locale)
    if slug:
        found.append(slug)
    needle = _norm_term(term)
    if needle:
        for candidate, area in registry.items():
            if candidate in found:
                continue
            terms = [candidate, area.get("name_ru") or "", area.get("name_en") or ""]
            aliases = area.get("aliases") or {}
            for loc in ("ru", "en"):
                terms.extend(aliases.get(loc) or [])
            if any(needle in _norm_term(t) for t in terms if t):
                found.append(candidate)
            if len(found) >= limit:
                break
    out: list[dict] = []
    for candidate in found[:limit]:
        area = registry.get(candidate) or {}
        out.append(
            {
                "code": candidate,
                "name_ru": area.get("name_ru"),
                "name_en": area.get("name_en"),
                "kind": area.get("kind"),
            }
        )
    return out


def _db_area_rows(db: Any, term: str, limit: int) -> list[dict]:
    """Area rows whose code/name/aliases match `term` (DB fallback shim)."""
    like = f"%{term.strip()}%"
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT code, name_ru, name_en, kind
              FROM areas
             WHERE name_ru ILIKE %s
                OR name_en ILIKE %s
                OR code ILIKE %s
                OR EXISTS (
                       SELECT 1 FROM unnest(aliases) AS a
                        WHERE a ILIKE %s
                   )
             ORDER BY kind, code
             LIMIT %s
            """,
            (like, like, like, like, limit),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _fetch(db: Any, fn: Callable[[Any], Any]) -> tuple[Any, str | None, str | None]:
    """Run `fn(conn)` on `db` or a short-lived connection. Never raises.

    Returns ``(value, error_code, message)``; `value` is None on failure.
    """
    conn = db
    close = False
    try:
        if conn is None:
            conn = _connect()
            close = True
        return fn(conn), None, None
    except ImportError as exc:
        log.warning("tool: db driver unavailable: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database driver not available"
    except Exception as exc:
        log.warning("tool: db access failed: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database not reachable"
    finally:
        if close and conn is not None:
            with suppress(Exception):
                conn.close()


def find_areas(term: str, locale: str = "ru") -> dict:
    """Resolve a territory name ("старый город", "Новогрудок") to area slugs.

    A name that matches nothing returns an empty result set — never a guessed radius.
    """
    return find_areas_with_db(None, term, locale)


def find_areas_with_db(db: Any, term: str, locale: str = "ru") -> dict:
    """`find_areas` reusing a caller-owned connection (used by the agent)."""
    provenance: dict[str, Any] = {
        "source": "areas.json",
        "locale": locale if locale in ("ru", "en") else "ru",
        "result_cap": MAX_AREAS_PER_CALL,
    }
    if not isinstance(term, str) or not term.strip():
        return _envelope(
            "find_areas",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="term must be a non-empty string",
        )

    try:
        found = _areas_from_registry(term, provenance["locale"], MAX_AREAS_PER_CALL)
    except ImportError:
        provenance["source"] = "areas (db fallback)"
        rows, error, message = _fetch(
            db, lambda conn: _db_area_rows(conn, term, MAX_AREAS_PER_CALL)
        )
        if error is not None:
            return _envelope("find_areas", [], provenance, error=error, message=message)
        results = [_project(row, _AREA_FIELDS) for row in (rows or [])[:MAX_AREAS_PER_CALL]]
        return _envelope("find_areas", results, provenance, capped=len(rows or []) > len(results))
    except Exception as exc:
        log.warning("find_areas: area registry unusable: %s", exc)
        return _envelope(
            "find_areas",
            [],
            provenance,
            error=ERR_AREA_REGISTRY,
            message="area registry is unusable; areas cannot be resolved",
        )

    results = [_project(row, _AREA_FIELDS) for row in found[:MAX_AREAS_PER_CALL]]
    return _envelope("find_areas", results, provenance, capped=len(found) >= MAX_AREAS_PER_CALL)


def search_places(
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_m: float | None = None,
) -> dict:
    """The ids are the only handles the agent may put into a requirement; unknown
    category codes are dropped, never sent to SQL.
    """
    return search_places_with_db(None, query, category_codes, limit, near_lat, near_lon, radius_m)


def search_places_with_db(
    db: Any,
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_m: float | None = None,
) -> dict:
    """`search_places` reusing a caller-owned connection (used by the agent)."""
    requested = [c for c in (category_codes or []) if isinstance(c, str) and c.strip()]
    db_cats = db_values(requested)
    dropped = [c for c in requested if c not in db_cats]

    want, limit_clamped = _clamp(limit, 1, MAX_PLACES_PER_SEARCH, DEFAULT_SEARCH_LIMIT)
    want = int(want)

    near: tuple[float, float, float] | None = None
    radius_clamped = False
    if isinstance(near_lat, (int, float)) and isinstance(near_lon, (int, float)):
        radius, radius_clamped = _clamp(radius_m, MIN_RADIUS_M, MAX_RADIUS_M, DEFAULT_RADIUS_M)
        near = (float(near_lat), float(near_lon), float(radius))

    provenance: dict[str, Any] = {
        "source": "places",
        "strategy": "nearby" if near else "keyword",
        "categories": db_cats,
        "dropped_codes": dropped,
        "result_cap": MAX_PLACES_PER_SEARCH,
        "radius_m": near[2] if near else None,
    }

    if not isinstance(query, str) or not query.strip():
        return _envelope(
            "search_places",
            [],
            provenance,
            error=ERR_EMPTY_QUERY,
            message="query must be a non-empty string",
        )

    def run(conn: Any) -> list[dict]:
        fetch = MAX_PLACES_PER_SEARCH if db_cats else want
        rows = _db_search_rows(conn, query=query.strip(), limit=fetch, near=near)
        if db_cats:
            rows = [r for r in rows if r.get("category") in db_cats]
        rows = [_project(r, _PLACE_FIELDS) for r in rows]
        return rows[:want]

    rows, error, message = _fetch(db, run)
    if error is not None:
        return _envelope("search_places", [], provenance, error=error, message=message)
    results = rows or []
    return _envelope(
        "search_places",
        results,
        provenance,
        capped=limit_clamped or radius_clamped,
    )


def get_place_facts(place_id: int) -> dict:
    """The provenance says so (`fact_status="raw_unverified"`); a missing place
    returns ``error="not_found"``.
    """
    return get_place_facts_with_db(None, place_id)


def get_place_facts_with_db(db: Any, place_id: int) -> dict:
    """`get_place_facts` reusing a caller-owned connection (used by the agent)."""
    provenance: dict[str, Any] = {
        "source": "places",
        "result_cap": MAX_FACTS_PER_CALL,
        "fact_status": "raw_unverified",
    }
    if not _positive_int(place_id):
        return _envelope(
            "get_place_facts",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="place_id must be a positive integer",
        )

    def run(conn: Any) -> list[dict]:
        row = _db_place_row(conn, place_id)
        if row is None:
            return []
        facts = _project(row, _FACT_FIELDS)
        links = row.get("links")
        facts["links"] = (
            list(links)[:MAX_LINKS_PER_PLACE] if isinstance(links, (list, tuple)) else None
        )
        return [facts]

    rows, error, message = _fetch(db, run)
    if error is not None:
        return _envelope("get_place_facts", [], provenance, error=error, message=message)
    if not rows:
        return _envelope(
            "get_place_facts",
            [],
            provenance,
            error=ERR_NOT_FOUND,
            message=f"no place with id {place_id}",
        )
    return _envelope("get_place_facts", rows[:MAX_FACTS_PER_CALL], provenance)


def services_near_route(
    category_codes: list[str] | None = None,
    shape: list | None = None,
    max_detour_minutes: float | None = None,
) -> dict:
    """NOT IMPLEMENTED — a deliberate gap: it answers ``error="not_implemented"``
    with an empty result set, so the agent treats the service as unproven.
    """
    codes = [c for c in (category_codes or []) if isinstance(c, str) and c.strip()]
    db_cats = db_values(codes)
    points = [p for p in (shape or []) if p is not None]
    detour, detour_clamped = _clamp(max_detour_minutes, 1, MAX_DETOUR_MINUTES, MAX_DETOUR_MINUTES)

    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "reason_code": ERR_NOT_IMPLEMENTED,
        "reason": "needs a real Valhalla route along `shape` to measure a detour",
        "result_cap": MAX_SERVICES_PER_CALL,
        "categories": db_cats,
        "dropped_codes": [c for c in codes if c not in db_cats],
        "points_in": len(points),
        "max_detour_minutes": int(detour),
        "detour_clamped": detour_clamped,
    }

    return _envelope(
        "services_near_route",
        [],
        provenance,
        capped=bool(len(points) > MAX_ROUTE_POINTS or detour_clamped),
        error=ERR_NOT_IMPLEMENTED,
        message=(
            "detour measurement is not implemented (no Valhalla route along the shape); "
            "the presence of a service cannot be confirmed by this tool"
        ),
    )
