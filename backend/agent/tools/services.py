"""``nearby_services`` — what can be inserted on the way, with a measured detour."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools._shared import (
    ERR_BAD_ARGUMENT,
    ERR_DB_UNAVAILABLE,
    ToolAnswer,
    clamp,
    envelope,
    fetch,
)
from core import constants
from core.errors import UpstreamUnavailableError
from routing.models import Candidate
from routing.stops import candidate_of
from routing.valhalla.matrix import time_matrix
from routing.valhalla.snap import snap_locations

if TYPE_CHECKING:
    from db.store.registry import Repositories

MAX_STOPS = 12
MAX_CANDIDATES = 12
MAX_RESULTS = 5
MAX_DETOUR_MINUTES = 30
DEFAULT_DETOUR_MINUTES = 10
DEFAULT_RADIUS_KM = 0.6
NEARBY_PER_STOP = 8


def _reachable(seconds: float) -> bool:
    """A leg we can actually measure: neither unknown (NaN) nor unreachable."""
    return math.isfinite(seconds) and seconds < constants.UNREACHABLE_S


def nearby_services(
    stop_ids: list[int],
    category_codes: list[str] | None = None,
    *,
    repos: Repositories | None,
    profile: str = "pedestrian",
    max_detour_minutes: float | None = None,
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Services insertable on this route, each with the walking detour it costs."""
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "profile": profile,
        "result_cap": MAX_RESULTS,
        "fact_status": "detour measured",
    }
    wanted = [i for i in (stop_ids or []) if i > 0][:MAX_STOPS]
    if len(wanted) < constants.MIN_LEG_POINTS:
        return envelope(
            "nearby_services",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="a service sits *between* stops: give at least two stop ids",
        )

    budget, budget_clamped = clamp(
        max_detour_minutes, 1, MAX_DETOUR_MINUTES, DEFAULT_DETOUR_MINUTES
    )
    rows, error, message = fetch(repos, lambda store: store.places.get_by_ids(wanted))
    stops = [candidate_of(row, 0.0) for row in (rows or [])]
    if error is None and len(stops) < constants.MIN_LEG_POINTS:
        error, message = ERR_DB_UNAVAILABLE, "fewer than two of the given stops exist"
    found: dict[int, Candidate] = {}
    if error is None:
        found, error, message = fetch(
            repos, lambda store: _collect_services(store, stops, category_codes)
        )
    if error is not None:
        return envelope("nearby_services", [], provenance, error=error, message=message)

    services = list(found.values())[:MAX_CANDIDATES]
    provenance["candidates"] = len(services)
    provenance["categories"] = category_codes or "every service the taxonomy knows"
    if not services:
        return envelope("nearby_services", [], provenance)

    budget_s = float(budget) * 60.0
    ranked, why = _ranked_by_detour(stops, services, profile, budget_s, deadline_s)
    if why is not None:
        provenance["reason_code"] = "valhalla_unavailable"
        return envelope("nearby_services", [], provenance, error="upstream", message=why)

    results = ranked[:MAX_RESULTS]
    return envelope(
        "nearby_services",
        results,
        provenance,
        capped=bool(budget_clamped or len(ranked) > len(results)),
    )


def _collect_services(
    store: Repositories, stops: list[Candidate], category_codes: list[str] | None
) -> dict[int, Candidate]:
    """Services near any stop, by category. Never a stop the route already has."""
    requested = [c for c in (category_codes or []) if c.strip()]
    wanted_codes = store.taxonomy.db_values(requested) if requested else None
    allowed = set(wanted_codes) if wanted_codes else None
    on_route = {stop.id for stop in stops}
    found: dict[int, Candidate] = {}
    near = store.places.nearby_many(
        [(stop.lat, stop.lon) for stop in stops],
        radius_km=DEFAULT_RADIUS_KM,
        limit_per_stop=NEARBY_PER_STOP,
    )
    for place in near:
        if place.id is None:
            continue
        if place.id in on_route or place.id in found:
            continue
        if allowed is not None and (place.category or "") not in allowed:
            continue
        found[place.id] = candidate_of(place, 0.0)
    return found


def _ranked_by_detour(
    stops: list[Candidate],
    services: list[Candidate],
    profile: str,
    budget_s: float,
    deadline_s: float | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Which services fit, cheapest detour first, or why Valhalla would not measure it."""
    points = [*stops, *services]
    coords = [{"lat": point.lat, "lon": point.lon} for point in points]
    try:
        snapped = snap_locations(coords, costing=profile)
        if len(snapped) != len(points):
            raise UpstreamUnavailableError("some stops could not be snapped to a road")
        matrix = time_matrix(snapped, snapped, costing=profile, deadline=deadline_s)
    except UpstreamUnavailableError as exc:
        return [], str(exc)
    ranked = _best_detour(stops, services, matrix, budget_s)
    ranked.sort(key=lambda item: item["detour_minutes"])
    return ranked, None


def _best_detour(
    stops: list[Candidate],
    services: list[Candidate],
    matrix: list[list[float]],
    budget_s: float,
) -> list[dict[str, Any]]:
    """The least walking each service would add, when it is worth adding at all."""
    offset = len(stops)
    ranked: list[dict[str, Any]] = []
    for position, service in enumerate(services):
        index = offset + position
        best: float | None = None
        after: int | None = None
        for i in range(len(stops) - 1):
            direct = matrix[i][i + 1]
            via = matrix[i][index] + matrix[index][i + 1]
            if not (_reachable(direct) and _reachable(via)):
                continue
            detour = max(0.0, via - direct)
            if best is None or detour < best:
                best, after = detour, i
        if best is None or best > budget_s:
            continue
        ranked.append(
            {
                "id": service.id,
                "name": service.name,
                "category": service.category,
                "town": service.town,
                "lat": service.lat,
                "lon": service.lon,
                "detour_minutes": round(best / 60.0, 1),
                "insert_after": stops[after].id if after is not None else stops[0].id,
            }
        )
    return ranked


def register_nearby_services(agent: Any) -> None:
    """Advertise ``nearby_services`` to the agent."""

    @agent.tool(name="nearby_services")
    def _nearby_services(
        ctx: RunContext[PlanningDeps],
        stop_ids: list[int],
        category_codes: list[str] | None = None,
        profile: str = "pedestrian",
        max_detour_minutes: float | None = None,
    ) -> ToolAnswer:
        """Services that can be slipped in on this route, with the real detour."""
        return nearby_services(
            stop_ids,
            category_codes,
            repos=ctx.deps.repos,
            profile=profile,
            max_detour_minutes=max_detour_minutes,
            deadline_s=ctx.deps.seconds_left(),
        )
