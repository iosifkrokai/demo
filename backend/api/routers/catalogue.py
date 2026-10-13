"""HTTP surface for the catalogue: the point list, ready-made routes, services."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from api import deps
from api.models.routes import ServicesAlongReq
from db.store import itineraries as itineraries_mod
from db.store.services import DEFAULT_PROFILE, MAX_SERVICES

log = logging.getLogger(__name__)

router = APIRouter(tags=["catalogue"])


@router.get("/places")
def places(request: Request) -> dict[str, Any]:
    """The full point catalogue — every place in the dataset, for the «все точки» tab."""
    return deps.get_repos(request).places.catalog()


@router.get("/routes/itineraries")
def itineraries(request: Request) -> dict[str, Any]:
    """Ready-made routes — curated, and resolved against the live dataset."""
    repos = deps.get_repos(request)
    items, missing = itineraries_mod.resolve_itineraries(
        repos.places, repos.taxonomy, repos.itineraries.authored()
    )
    return {"items": items, "missing": missing}


@router.post("/routes/services")
def services_along_route(req: ServicesAlongReq, request: Request) -> dict[str, Any]:
    """Secondary points beside the line: cafés, toilets, hotels — never stops."""
    repos = deps.get_repos(request)
    try:
        return repos.places.services_along(
            req.shape,
            categories=req.categories,
            service_category_codes=repos.taxonomy.service_codes(),
            profile=req.profile or DEFAULT_PROFILE,
            max_off_line_m=req.max_off_line_m,
            limit=req.limit or MAX_SERVICES,
        )
    except ValueError as exc:
        raise deps.http_error(422, str(exc)) from exc
