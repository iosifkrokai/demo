"""HTTP surface for the catalogue: the point list, ready-made routes, services.

A browse, not a search: no model is involved and nothing is capped by a query.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request

from api import deps
from core.errors import ItinerariesUnavailable
from db.store import itineraries as itineraries_mod
from db.store.services import DEFAULT_PROFILE, MAX_SERVICES
from planner.models import ServicesAlongReq
from telemetry import trace

log = logging.getLogger(__name__)

router = APIRouter(tags=["catalogue"])


@router.get("/places")
def places(request: Request) -> dict:
    """The full point catalogue — every place in the dataset, for the «все точки» tab.

    A browse, not a search: no model is involved and nothing is capped by a query.
    """
    return deps.get_repos(request).places.catalog()


@router.get("/routes/itineraries")
def itineraries(request: Request) -> dict:
    """Ready-made routes — curated, and resolved against the live dataset.

    `missing` names any stop key that no longer resolves.
    """
    try:
        items, missing = itineraries_mod.resolve_itineraries(
            deps.get_repos(request).places
        )
    except ItinerariesUnavailable as exc:
        log.error("itineraries unavailable: %s", exc)
        raise deps.http_error(503, "itineraries_unavailable") from exc
    return {"items": items, "missing": missing}


@router.post("/routes/services")
def services_along_route(req: ServicesAlongReq, request: Request) -> dict:
    """Secondary points beside the line: cafés, toilets, hotels — never stops.

    Distance and position are measured; items carry `detour_confirmed: false`.
    """
    trace.begin(uuid.uuid4().hex, session_id=req.session_id)
    try:
        try:
            answer = deps.get_repos(request).places.services_along(
                req.shape,
                categories=req.categories,
                profile=req.profile or DEFAULT_PROFILE,
                max_off_line_m=req.max_off_line_m,
                limit=req.limit or MAX_SERVICES,
            )
        except ValueError as exc:
            trace.record(
                "services",
                "error",
                input={"categories": req.categories, "limit": req.limit},
                reason=str(exc),
            )
            raise deps.http_error(422, str(exc)) from exc
        trace.record(
            "services",
            input={"categories": req.categories, "limit": req.limit},
            found=len(answer["items"]),
            capped=answer["capped"],
        )
        return answer
    finally:
        trace.finish()
