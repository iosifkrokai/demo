"""Bounded tool surface for the interpretation agent (spec 002 §4.2).

The interpretation agent may call exactly these four tools and nothing else:
no arbitrary SQL, no URL fetching, no DB writes.  Every tool

  * validates and clamps its own arguments (the model cannot widen a limit),
  * enforces a hard result cap and reports it (`capped`, `provenance.result_cap`),
  * returns structured data *plus provenance* — where a row came from and how
    trustworthy the fact is,
  * never raises at the caller: a dead DB or a bad argument becomes an
    ``error`` code in the returned dict, so a broken tool degrades the agent
    instead of failing the request.

Why a plain module and not `agent.tool` decorators
--------------------------------------------------
This file has no PydanticAI import on purpose.  The agent wiring lives in
``agent/planner/agent_interpret.py``, which registers these functions as
tools; keeping the surface free of the framework means it can be exercised
(and its caps pinned) offline, with no model and no database.

Honest gaps
-----------
``services_near_route`` is a refusal, not an implementation: a real detour in
minutes needs a Valhalla route along the shape (spec §4.3) and this process has
no route to measure.  It returns ``error="not_implemented"`` with an empty
result set — "no answer" is honest, a made-up detour is not.

Integration
-----------
``find_areas`` resolves through ``agent.areas`` / ``data/areas.json`` — the
versioned single source of area definitions. If that module is absent the tool
falls back to the ``areas`` table (db/migrations/0004_places_taxonomy.sql,
loaded by ``python -m seed``); if the registry exists but is unusable, the
tool answers ``error="area_registry_unavailable"`` rather than guessing.
``places.category`` is still a free-text column, so category values are mapped
through ``taxonomy.db_values`` — the single source of codes — and unknown codes
are reported as dropped, never passed to SQL.

Two entry points per tool
-------------------------
``search_places(...)`` opens its own short-lived connection; the
``*_with_db`` twin reuses a connection the caller already owns (the agent
registers those, so one interpretation does not open four connections).  The
model never sees a connection: it is not a parameter of the registered tool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from core.config import DSN
from domain.taxonomy import db_values

log = logging.getLogger(__name__)

# Hard caps
# These are the contract: a limit the model sends is clamped to them, and the
# clamp is reported back. They exist so one misread query cannot pull the whole
# region into the model's context.
MAX_PLACES_PER_SEARCH = 8
MAX_FACTS_PER_CALL = 1  # get_place_facts answers about ONE place
MAX_LINKS_PER_PLACE = 3
MAX_AREAS_PER_CALL = 8
MAX_SERVICES_PER_CALL = 5
MAX_ROUTE_POINTS = 64  # shape points accepted by services_near_route
MAX_DETOUR_MINUTES = 30
MIN_RADIUS_M = 100.0
MAX_RADIUS_M = 20_000.0
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_RADIUS_M = 1_000.0

# Error codes (machine-readable; localization happens in the API layer).
ERR_EMPTY_QUERY = "empty_query"
ERR_BAD_ARGUMENT = "bad_argument"
ERR_NOT_FOUND = "not_found"
ERR_DB_UNAVAILABLE = "db_unavailable"
ERR_AREA_REGISTRY = "area_registry_unavailable"
ERR_NOT_IMPLEMENTED = "not_implemented"

# How long a tool waits for a connection before giving up. A tool is a
# convenience for the model, never a reason to hang a request.
DB_TIMEOUT_S = 3.0

# The bounded projection of a `places` row this surface exposes. Anything else
# (embedding, description, internal counters) stays out of the model's context.
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


# Result envelopes


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

    A missing/unparseable value becomes `default` (not an error): the model
    simply may not widen the cap, and anything it cannot parse is a default.
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


# Connection / query seams (monkeypatched in tests)


def _connect() -> Any:
    """Open a short-lived psycopg connection.

    Imported lazily so importing this module never requires a driver or a
    reachable database; the caller decides what a failure means.
    """
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

    Reuses ``search.nearby_places`` when the model gave a point, otherwise the
    trigram/keyword search; both are the same signals the deterministic
    pipeline uses, so the agent cannot see more of the DB than the pipeline.
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
    # RUF001: the Cyrillic characters below are the point (yo -> ye fold).
    return " ".join(str(text).lower().replace("ё", "е").split())


def _areas_from_registry(term: str, locale: str, limit: int) -> list[dict]:
    """Canonical area slugs from the versioned registry (``agent/areas.py``).

    ``data/areas.json`` is the single source of area definitions (spec §5), so
    this is the primary path and the DB table is only a fallback. May raise —
    the caller decides what an unusable registry means.
    """
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
    """Area rows whose code/name/aliases match `term` (SHIM — see module docs)."""
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

    Returns ``(value, error_code, message)``; `value` is None on failure. Every
    failure mode — no driver, no DB, timeout, bug in a query — becomes an error
    code the agent can read, because an exception here would abort the whole
    interpretation for a reason the model cannot act on.
    """
    conn = db
    close = False
    try:
        if conn is None:
            conn = _connect()
            close = True
        return fn(conn), None, None
    except ImportError as exc:  # driver missing — deployment fact, not a bug
        log.warning("tool: db driver unavailable: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database driver not available"
    except Exception as exc:
        log.warning("tool: db access failed: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database not reachable"
    finally:
        if close and conn is not None:
            # Closing must never mask the answer we already have.
            with suppress(Exception):
                conn.close()


# Tools


def find_areas(term: str, locale: str = "ru") -> dict:
    """Resolve a territory name ("старый город", "Новогрудок") to area slugs.

    Returns canonical area codes the request may be restricted to. A name that
    matches nothing returns an empty result set — never a guessed radius.
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
        # No registry module in this deployment: fall back to the DB copy of
        # the same areas (db/migrations/0004_places_taxonomy.sql).
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
    """Search real places in the Grodno region by text and/or category.

    Returns ids, names, coordinates and category codes — the ids are the only
    handles the agent may put into a requirement. The result set is capped at
    ``MAX_PLACES_PER_SEARCH``; `capped=true` means the request asked for more.
    Unknown category codes are dropped (and reported), never sent to SQL.
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
        # Over-fetch only up to the hard cap: the category filter is applied in
        # Python (places.category is free text), so a strict limit before
        # filtering could return nothing while matching rows exist.
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
    """Raw, unverified facts about ONE place: hours, price, town, source URL.

    These are exactly the values the database holds (OSM/dataset raw strings).
    They are NOT an endorsement of current opening hours or ticket prices, and
    the provenance says so (`fact_status="raw_unverified"`). A place that does
    not exist returns ``error="not_found"``.
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
    """Services (toilets, cafés …) within a detour of a route shape.

    NOT IMPLEMENTED — this is a deliberate, declared gap. A detour is a real
    Valhalla route along the geometry (spec §4.3); without one, any number this
    tool returned would be invented. It answers ``error="not_implemented"``
    with an empty result set, so the agent must treat the service as unproven
    and say so instead of promising a stop.
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
