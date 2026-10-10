"""HTTP surface for the anonymous client entity.

The wire speaks codes; a missing ``X-Client-Id`` is not an error, a malformed one is.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from api import deps
from api.models.clients import (
    CLIENT_ID_HEADER,
    REASON_INVALID_CLIENT_ID,
    REASON_ROUTE_NOT_FOUND,
    REASON_STORAGE_UNAVAILABLE,
    REASON_TOO_MANY_ROUTES,
    PreferencesIn,
    PreferencesOut,
    RouteCreated,
    RouteCreateIn,
    RouteDetail,
    RouteListItem,
    RoutePatchIn,
)
from db.store.clients_store import (
    MAX_SAVED_ROUTES,
    ClientRepository,
    TooManyRoutes,
)

router = APIRouter(prefix="/clients/me", tags=["clients"])


def _client_or_error(
    request: Request,
) -> tuple[uuid.UUID | None, JSONResponse | None]:
    """Parse ``X-Client-Id``.

    ``(None, None)`` when absent (work without saving); ``(None, 400)`` when malformed.
    """
    raw = request.headers.get(CLIENT_ID_HEADER)
    if raw is None or not raw.strip():
        return None, None
    try:
        return uuid.UUID(raw.strip()), None
    except (ValueError, AttributeError):
        return None, deps.error(400, REASON_INVALID_CLIENT_ID)


@router.get("/preferences", response_model=PreferencesOut)
@deps.storage_guarded
def get_preferences(
    request: Request, repo: ClientRepository = Depends(deps.get_client_repository)
) -> Any:
    """Saved preferences; a missing field comes back as null/empty."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return PreferencesOut()
    repo.ensure_client(client_id)
    stored = repo.get_preferences(client_id)
    return PreferencesOut(**(stored or {}))


@router.put("/preferences", response_model=PreferencesOut)
@deps.storage_guarded
def put_preferences(
    body: PreferencesIn,
    request: Request,
    repo: ClientRepository = Depends(deps.get_client_repository),
) -> Any:
    """Partial update: touch only the sent fields; ``null`` clears one."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return deps.error(503, REASON_STORAGE_UNAVAILABLE)
    fields = body.model_dump(exclude_unset=True)
    stored = repo.upsert_preferences(client_id, fields)
    return PreferencesOut(**stored)


@router.post("/routes", response_model=RouteCreated, status_code=201)
@deps.storage_guarded
def create_route(
    body: RouteCreateIn,
    request: Request,
    repo: ClientRepository = Depends(deps.get_client_repository),
) -> Any:
    """Persist a checked plan verbatim; the id is minted here."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return deps.error(503, REASON_STORAGE_UNAVAILABLE)
    try:
        created = repo.add_route(
            client_id,
            uuid.uuid4(),
            query=body.query,
            plan=body.plan,
            name=body.name,
            visit_overrides=body.visit_overrides,
        )
    except TooManyRoutes:
        return deps.error(409, REASON_TOO_MANY_ROUTES)
    return RouteCreated(**created)


@router.get("/routes", response_model=list[RouteListItem])
@deps.storage_guarded
def list_routes(
    request: Request,
    limit: int = Query(default=50, ge=1, le=MAX_SAVED_ROUTES),
    repo: ClientRepository = Depends(deps.get_client_repository),
) -> Any:
    """Newest first, without heavy geometry (stop_count/distance/duration)."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return []
    repo.ensure_client(client_id)
    return [RouteListItem(**row) for row in repo.list_routes(client_id, limit)]


@router.get("/routes/{route_id}", response_model=RouteDetail)
@deps.storage_guarded
def get_route(
    route_id: str,
    request: Request,
    repo: ClientRepository = Depends(deps.get_client_repository),
) -> Any:
    """The full saved record: plan and visit_overrides included."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    rid = deps.parse_uuid(route_id)
    if client_id is None or rid is None:
        return deps.error(404, REASON_ROUTE_NOT_FOUND)
    repo.ensure_client(client_id)
    row = repo.get_route(client_id, rid)
    if row is None:
        return deps.error(404, REASON_ROUTE_NOT_FOUND)
    return RouteDetail(**row)


@router.patch("/routes/{route_id}", response_model=RouteDetail)
@deps.storage_guarded
def rename_route(
    route_id: str,
    body: RoutePatchIn,
    request: Request,
    repo: ClientRepository = Depends(deps.get_client_repository),
) -> Any:
    """Rename a saved route; nothing else about it changes."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    rid = deps.parse_uuid(route_id)
    if client_id is None or rid is None:
        return deps.error(404, REASON_ROUTE_NOT_FOUND)
    row = repo.rename_route(client_id, rid, body.name)
    if row is None:
        return deps.error(404, REASON_ROUTE_NOT_FOUND)
    return RouteDetail(**row)


@router.delete("/routes/{route_id}", status_code=204, response_model=None)
@deps.storage_guarded
def delete_route(
    route_id: str,
    request: Request,
    repo: ClientRepository = Depends(deps.get_client_repository),
) -> Any:
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    rid = deps.parse_uuid(route_id)
    if client_id is None or rid is None:
        return deps.error(404, REASON_ROUTE_NOT_FOUND)
    if not repo.delete_route(client_id, rid):
        return deps.error(404, REASON_ROUTE_NOT_FOUND)
    return Response(status_code=204)


@router.delete("", status_code=204, response_model=None)
@deps.storage_guarded
def delete_client(
    request: Request, repo: ClientRepository = Depends(deps.get_client_repository)
) -> Any:
    """Delete the client; routes and preferences cascade away with it."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return deps.error(503, REASON_STORAGE_UNAVAILABLE)
    repo.delete_client(client_id)
    return Response(status_code=204)
