"""HTTP surface for the anonymous client entity (spec 003 §3).

All routes live under ``/clients/me`` and are registered as one APIRouter so a
single line in main.py wires the whole feature in.  Two rules shape every
handler:

* **The wire speaks codes, not prose.**  Failures answer
  ``{"reason": "<code>"}`` with one of ``storage_unavailable``,
  ``route_not_found``, ``invalid_client_id``, ``too_many_routes``.  All human
  text belongs to the UI.
* **A missing ``X-Client-Id`` is not an error** (§1).  Reads return the empty
  state; writes answer ``503 storage_unavailable`` — honest "saving is not
  available without an identity", which is exactly what the UI tells the
  tourist.  A *present but malformed* id is a different thing and answers
  ``400 invalid_client_id``.

Storage failures are translated in one place (:func:`_storage_guarded`): any
:class:`~agent.clients_store.StorageUnavailable` becomes ``503`` so a database
outage can never reach the browser as an opaque 500 and the client keeps
working from local storage.
"""

from __future__ import annotations

import functools
import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from .clients_models import (
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
from .clients_store import (
    MAX_SAVED_ROUTES,
    ClientRepository,
    PostgresClientRepository,
    StorageUnavailable,
    TooManyRoutes,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/clients/me", tags=["clients"])

_default_repo_lock = threading.Lock()


def get_repository(request: Request) -> ClientRepository:
    """The process-wide repository, or a test-injected fake.

    Tests set ``app.state.clients_repository``; nothing else needs to know
    whether storage is a live Postgres or a fake.
    """
    repo = getattr(request.app.state, "clients_repository", None)
    if repo is None:
        with _default_repo_lock:
            repo = getattr(request.app.state, "clients_repository", None)
            if repo is None:
                repo = PostgresClientRepository()
                request.app.state.clients_repository = repo
    return repo


def _error(status_code: int, reason: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"reason": reason})


def _storage_guarded(fn):
    """Turn a storage outage into ``503 storage_unavailable``, never a 500."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except StorageUnavailable as exc:
            log.warning("client storage unavailable: %s", exc)
            return _error(503, REASON_STORAGE_UNAVAILABLE)

    return wrapper


def _client_or_error(
    request: Request,
) -> tuple[uuid.UUID | None, JSONResponse | None]:
    """Parse ``X-Client-Id``.

    Returns ``(uuid, None)`` for a well-formed id, ``(None, None)`` when the
    header is absent (work without saving), and ``(None, 400)`` when it is
    present but not a UUID.
    """
    raw = request.headers.get(CLIENT_ID_HEADER)
    if raw is None or not raw.strip():
        return None, None
    try:
        return uuid.UUID(raw.strip()), None
    except (ValueError, AttributeError):
        return None, _error(400, REASON_INVALID_CLIENT_ID)


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


# Preferences

@router.get("/preferences", response_model=PreferencesOut)
@_storage_guarded
def get_preferences(
    request: Request, repo: ClientRepository = Depends(get_repository)
) -> Any:
    """Saved preferences; a missing field comes back as null/empty."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        # No identity: there is nothing saved *for this client*. The empty
        # state is the honest answer, not an error.
        return PreferencesOut()
    repo.ensure_client(client_id)
    stored = repo.get_preferences(client_id)
    return PreferencesOut(**(stored or {}))


@router.put("/preferences", response_model=PreferencesOut)
@_storage_guarded
def put_preferences(
    body: PreferencesIn,
    request: Request,
    repo: ClientRepository = Depends(get_repository),
) -> Any:
    """Partial update: touch only the sent fields; ``null`` clears one."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        # Writing needs an identity; without one, saving is simply unavailable.
        return _error(503, REASON_STORAGE_UNAVAILABLE)
    # exclude_unset is the whole point: absent fields stay untouched, explicit
    # nulls are present in the dict and clear their column.
    fields = body.model_dump(exclude_unset=True)
    stored = repo.upsert_preferences(client_id, fields)
    return PreferencesOut(**stored)


# Saved routes

@router.post("/routes", response_model=RouteCreated, status_code=201)
@_storage_guarded
def create_route(
    body: RouteCreateIn,
    request: Request,
    repo: ClientRepository = Depends(get_repository),
) -> Any:
    """Persist a checked plan verbatim; the id is minted here."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return _error(503, REASON_STORAGE_UNAVAILABLE)
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
        return _error(409, REASON_TOO_MANY_ROUTES)
    return RouteCreated(**created)


@router.get("/routes", response_model=list[RouteListItem])
@_storage_guarded
def list_routes(
    request: Request,
    limit: int = Query(default=50, ge=1, le=MAX_SAVED_ROUTES),
    repo: ClientRepository = Depends(get_repository),
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
@_storage_guarded
def get_route(
    route_id: str,
    request: Request,
    repo: ClientRepository = Depends(get_repository),
) -> Any:
    """The full saved record: plan and visit_overrides included."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    rid = _parse_uuid(route_id)
    if client_id is None or rid is None:
        # A malformed id is indistinguishable from one that is not ours.
        return _error(404, REASON_ROUTE_NOT_FOUND)
    repo.ensure_client(client_id)
    row = repo.get_route(client_id, rid)
    if row is None:
        return _error(404, REASON_ROUTE_NOT_FOUND)
    return RouteDetail(**row)


@router.patch("/routes/{route_id}", response_model=RouteDetail)
@_storage_guarded
def rename_route(
    route_id: str,
    body: RoutePatchIn,
    request: Request,
    repo: ClientRepository = Depends(get_repository),
) -> Any:
    """Rename a saved route; nothing else about it changes."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    rid = _parse_uuid(route_id)
    if client_id is None or rid is None:
        return _error(404, REASON_ROUTE_NOT_FOUND)
    row = repo.rename_route(client_id, rid, body.name)
    if row is None:
        return _error(404, REASON_ROUTE_NOT_FOUND)
    return RouteDetail(**row)


@router.delete("/routes/{route_id}", status_code=204, response_model=None)
@_storage_guarded
def delete_route(
    route_id: str,
    request: Request,
    repo: ClientRepository = Depends(get_repository),
) -> Any:
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    rid = _parse_uuid(route_id)
    if client_id is None or rid is None:
        return _error(404, REASON_ROUTE_NOT_FOUND)
    if not repo.delete_route(client_id, rid):
        return _error(404, REASON_ROUTE_NOT_FOUND)
    return Response(status_code=204)


# The client itself (spec §5 — "удалить мои данные")

@router.delete("", status_code=204, response_model=None)
@_storage_guarded
def delete_client(
    request: Request, repo: ClientRepository = Depends(get_repository)
) -> Any:
    """Delete the client; routes and preferences cascade away with it."""
    client_id, err = _client_or_error(request)
    if err is not None:
        return err
    if client_id is None:
        return _error(503, REASON_STORAGE_UNAVAILABLE)
    repo.delete_client(client_id)
    return Response(status_code=204)
