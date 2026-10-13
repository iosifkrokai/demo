"""``nearby_services`` — what can be inserted on the way, with a measured detour."""

from __future__ import annotations

import math
from typing import Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools import (
    ERR_BAD_ARGUMENT,
    ERR_DB_UNAVAILABLE,
    Remember,
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

MAX_STOPS = 12
MAX_CANDIDATES = 12
MAX_RESULTS = 5
MAX_DETOUR_MINUTES = 30
DEFAULT_DETOUR_MINUTES = 10
DEFAULT_RADIUS_KM = 0.6
NEARBY_PER_STOP = 8
UNREACHABLE_S = 1e8


def _reachable(seconds: float) -> bool:
    """A leg we can actually measure: neither unknown (NaN) nor unreachable."""
    return math.isfinite(seconds) and seconds < UNREACHABLE_S


def nearby_services(
    stop_ids: list[int],
    category_codes: list[str] | None = None,
    *,
    repos: Any,
    profile: str = "pedestrian",
    max_detour_minutes: float | None = None,
    limit: int = MAX_RESULTS,
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
    want, want_clamped = clamp(limit, 1, MAX_RESULTS, MAX_RESULTS)

    rows, error, message = fetch(repos, lambda store: store.places.get_by_ids(wanted))
    if error is not None:
        return envelope("nearby_services", [], provenance, error=error, message=message)

    stops = [candidate_of(row, 0.0) for row in (rows or [])]
    if len(stops) < constants.MIN_LEG_POINTS:
        return envelope(
            "nearby_services",
            [],
            provenance,
            error=ERR_DB_UNAVAILABLE,
            message="fewer than two of the given stops exist",
        )

    def collect(store: Any) -> dict[int, Candidate]:
        """Services near any stop, by category. Never a stop the route already has."""
        requested = [c for c in (category_codes or []) if c.strip()]
        wanted_codes = store.taxonomy.db_values(requested) if requested else None
        allowed = set(wanted_codes) if wanted_codes else None
        on_route = {stop.id for stop in stops}
        found: dict[int, Candidate] = {}
        for stop in stops:
            near = store.places.nearby(
                stop.lat, stop.lon, radius_km=DEFAULT_RADIUS_KM, limit=NEARBY_PER_STOP
            )
            for place in near:
                if place.id in on_route or place.id in found:
                    continue
                if allowed is not None and (place.category or "") not in allowed:
                    continue
                found[place.id] = candidate_of(place, 0.0)
        return found

    candidates, error, message = fetch(repos, collect)
    if error is not None:
        return envelope("nearby_services", [], provenance, error=error, message=message)
    services = list((candidates or {}).values())[:MAX_CANDIDATES]
    provenance["candidates"] = len(services)
    provenance["categories"] = category_codes or "every service the taxonomy knows"
    if not services:
        return envelope("nearby_services", [], provenance)

    points = [*stops, *services]
    coords = [{"lat": point.lat, "lon": point.lon} for point in points]
    try:
        snapped = snap_locations(coords, costing=profile)
        if len(snapped) != len(points):
            raise UpstreamUnavailableError("some stops could not be snapped to a road")
        matrix = time_matrix(snapped, snapped, costing=profile, deadline=deadline_s)
    except UpstreamUnavailableError as exc:
        provenance["reason_code"] = "valhalla_unavailable"
        return envelope("nearby_services", [], provenance, error="upstream", message=str(exc))

    budget_s = float(budget) * 60.0
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

    ranked.sort(key=lambda item: item["detour_minutes"])
    results = ranked[: int(want)]
    return envelope(
        "nearby_services",
        results,
        provenance,
        capped=bool(budget_clamped or want_clamped or len(ranked) > len(results)),
    )


def register_nearby_services(agent: Any, remember: Remember) -> None:
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
        return remember(
            ctx,
            nearby_services(
                stop_ids,
                category_codes,
                repos=ctx.deps.repos,
                profile=profile,
                max_detour_minutes=max_detour_minutes,
                deadline_s=ctx.deps.seconds_left(),
            ),
        )
