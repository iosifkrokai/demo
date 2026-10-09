"""HTTP surface for accounts, visits and the admin panel (spec 005).

Three groups of routes, registered as one APIRouter so a single line in main.py
wires the whole capability in:

* ``/auth/*``     — register / login / logout / me. Public; identity travels in an
                    HttpOnly cookie (``grodno_session``), never in JS-readable state.
* ``/me/visited`` — the signed-in tourist's durable «я здесь был» registry.
* ``/admin/*``    — user and place management; every handler needs ``role=admin``.

Three rules shape the handlers:

* **The wire speaks codes, not prose.** Failures answer ``{"reason": "<code>"}``;
  all human text belongs to the UI.
* **A missing identity is not an error; a wrong one is.** A read with no cookie is
  the empty state, but a write with no session is ``401 not_authenticated``, and a
  valid ``user`` account hitting ``/admin`` is ``403 not_admin`` — never a bare 500.
* **Storage failures are typed.** Any
  :class:`~agent.accounts_store.StorageUnavailable` becomes ``503
  storage_unavailable`` (see :func:`_storage_guarded`).
"""

from __future__ import annotations

import functools
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from contracts.accounts import (
    REASON_EMAIL_TAKEN,
    REASON_INVALID_CREDENTIALS,
    REASON_INVALID_EMAIL,
    REASON_INVALID_REQUEST,
    REASON_LAST_ADMIN,
    REASON_NOT_ADMIN,
    REASON_NOT_AUTHENTICATED,
    REASON_PLACE_NOT_FOUND,
    REASON_SELF_DELETE,
    REASON_SELF_ROLE,
    REASON_SOURCE_TAKEN,
    REASON_STORAGE_UNAVAILABLE,
    REASON_USER_NOT_FOUND,
    ROLE_ADMIN,
    SESSION_COOKIE,
    AdminPlaceIn,
    AdminPlaceListOut,
    AdminPlacePatch,
    AdminUserItem,
    AdminUserListOut,
    AdminUserPatch,
    AuthMeOut,
    LoginIn,
    PlaceItem,
    PublicUser,
    RegisterIn,
    StatsOut,
    VisitedBulkIn,
    VisitedBulkOut,
    VisitedItem,
    VisitedListOut,
    normalize_email,
    password_problem,
    public_user,
)
from contracts.clients import CLIENT_ID_HEADER
from domain.passwords import (
    SESSION_TTL_S,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)
from store.accounts_store import (
    MAX_LIST_LIMIT,
    AccountRepository,
    DuplicateSource,
    EmailTaken,
    PostgresAccountRepository,
    StorageUnavailable,
    place_payloads,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["accounts"])

_default_repo_lock = threading.Lock()


def get_repository(request: Request) -> AccountRepository:
    """The process-wide repository, or a test-injected fake.

    Tests set ``app.state.accounts_repository``; nothing else needs to know
    whether storage is a live Postgres or a fake.
    """
    repo = getattr(request.app.state, "accounts_repository", None)
    if repo is None:
        with _default_repo_lock:
            repo = getattr(request.app.state, "accounts_repository", None)
            if repo is None:
                repo = PostgresAccountRepository()
                request.app.state.accounts_repository = repo
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
            log.warning("account storage unavailable: %s", exc)
            return _error(503, REASON_STORAGE_UNAVAILABLE)

    return wrapper


# Identity helpers

def _session_token(request: Request) -> str | None:
    """The session token from the cookie, or a ``Bearer`` header (scripts/curl)."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie and cookie.strip():
        return cookie.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return None


def _session_user(
    request: Request, repo: AccountRepository
) -> dict[str, Any] | None:
    token = _session_token(request)
    if token is None:
        return None
    return repo.get_session_user(hash_token(token))


def _require_user(
    request: Request, repo: AccountRepository
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    user = _session_user(request, repo)
    if user is None:
        return None, _error(401, REASON_NOT_AUTHENTICATED)
    return user, None


def _require_admin(
    request: Request, repo: AccountRepository
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    user, err = _require_user(request, repo)
    if err is not None:
        return None, err
    assert user is not None
    if user["role"] != ROLE_ADMIN:
        return None, _error(403, REASON_NOT_ADMIN)
    return user, None


def _optional_client_id(request: Request) -> uuid.UUID | None:
    """The browser's anonymous ``X-Client-Id``, when it is a well-formed UUID."""
    raw = request.headers.get(CLIENT_ID_HEADER)
    if raw is None or not raw.strip():
        return None
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError):
        return None


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _start_session(
    request: Request,
    response: Response,
    repo: AccountRepository,
    user_id: uuid.UUID,
) -> None:
    """Mint a session, store only its hash, and hand the token to the browser."""
    token = new_session_token()
    expires = datetime.now(UTC) + timedelta(seconds=SESSION_TTL_S)
    repo.create_session(hash_token(token), user_id, expires)
    repo.touch_login(user_id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        path="/",
        # Secure only over https: a Secure cookie on plain http is dropped by the
        # browser, which would silently break login on the demo's http origin.
        secure=request.url.scheme == "https",
    )


# Auth

@router.post("/auth/register", response_model=PublicUser, status_code=201)
@_storage_guarded
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    """Create an account (always ``role=user``) and sign it in.

    The first administrator is made by ``python -m seed admin``, not by
    registering first — «first caller wins admin» is a hole, not a feature.
    """
    email = normalize_email(body.email)
    if email is None:
        return _error(422, REASON_INVALID_EMAIL)
    problem = password_problem(body.password)
    if problem is not None:
        return _error(422, problem)
    display_name = (body.display_name or "").strip() or None
    try:
        row = repo.create_user(
            uuid.uuid4(),
            email=email,
            password_hash=hash_password(body.password),
            display_name=display_name,
            role="user",
            client_id=_optional_client_id(request),
        )
    except EmailTaken:
        return _error(409, REASON_EMAIL_TAKEN)
    _start_session(request, response, repo, row["id"])
    return public_user(row)


@router.post("/auth/login", response_model=PublicUser)
@_storage_guarded
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    email = normalize_email(body.email)
    row = repo.get_user_by_email(email) if email is not None else None
    # One message for «no such user» and «wrong password»: distinguishing them
    # would let anyone probe which addresses have accounts.
    if row is None or not verify_password(body.password, row.get("password_hash")):
        return _error(401, REASON_INVALID_CREDENTIALS)
    client_id = _optional_client_id(request)
    if client_id is not None:
        repo.link_client(row["id"], client_id)
    _start_session(request, response, repo, row["id"])
    return public_user(row)


@router.post("/auth/logout", status_code=204, response_model=None)
@_storage_guarded
def logout(
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    # The response is built here, not injected: clearing the cookie has to travel
    # on the very response that drops the session row.
    out = Response(status_code=204)
    token = _session_token(request)
    if token is not None:
        repo.delete_session(hash_token(token))
    out.delete_cookie(SESSION_COOKIE, path="/")
    return out


@router.get("/auth/me", response_model=AuthMeOut)
@_storage_guarded
def me(
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    """Honest about the anonymous case: ``{authenticated: false}``, not a 401.

    The SPA calls this on startup; a 401 here would be noise in the console for
    every visitor who has simply not logged in yet.
    """
    user = _session_user(request, repo)
    if user is None:
        return AuthMeOut(authenticated=False, user=None)
    return AuthMeOut(authenticated=True, user=public_user(user))


# Visits

@router.get("/me/visited", response_model=VisitedListOut)
@_storage_guarded
def list_visited(
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    user, err = _require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    rows = repo.list_visited(user["id"])
    payloads = place_payloads(rows)
    items = [
        VisitedItem(**payload, visited_at=row["visited_at"])
        for payload, row in zip(payloads, rows)
    ]
    return VisitedListOut(items=items, count=len(items))


@router.put("/me/visited/{place_id}", response_model=VisitedItem)
@_storage_guarded
def mark_visited(
    place_id: int,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    user, err = _require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    marked = repo.mark_visited(user["id"], place_id)
    place = repo.get_place(place_id) if marked is not None else None
    if marked is None or place is None:
        return _error(404, REASON_PLACE_NOT_FOUND)
    payload = place_payloads([place])[0]
    return VisitedItem(**payload, visited_at=marked["visited_at"])


@router.post("/me/visited", response_model=VisitedBulkOut)
@_storage_guarded
def mark_visited_bulk(
    body: VisitedBulkIn,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    user, err = _require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    marked = repo.mark_visited_many(user["id"], body.place_ids)
    return VisitedBulkOut(marked=marked, count=len(marked))


@router.delete("/me/visited/{place_id}", status_code=204, response_model=None)
@_storage_guarded
def unmark_visited(
    place_id: int,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    user, err = _require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    repo.unmark_visited(user["id"], place_id)  # idempotent: absent is fine
    return Response(status_code=204)


# Admin — users

@router.get("/admin/users", response_model=AdminUserListOut)
@_storage_guarded
def admin_list_users(
    request: Request,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    _, err = _require_admin(request, repo)
    if err is not None:
        return err
    rows, total = repo.list_users(q=q.strip(), limit=limit, offset=offset)
    return AdminUserListOut(
        items=[AdminUserItem(**row) for row in rows], total=total
    )


@router.patch("/admin/users/{user_id}", response_model=PublicUser)
@_storage_guarded
def admin_patch_user(
    user_id: str,
    body: AdminUserPatch,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    actor, err = _require_admin(request, repo)
    if err is not None:
        return err
    assert actor is not None
    target_id = _parse_uuid(user_id)
    if target_id is None:
        return _error(404, REASON_USER_NOT_FOUND)
    target = repo.get_user(target_id)
    if target is None:
        return _error(404, REASON_USER_NOT_FOUND)

    fields = body.model_dump(exclude_unset=True)
    role = fields.get("role")
    display_name_set = "display_name" in fields

    if role is not None and role != target["role"]:
        if target_id == actor["id"]:
            return _error(409, REASON_SELF_ROLE)
        if target["role"] == ROLE_ADMIN and role != ROLE_ADMIN:
            # Never leave the system with no administrator.
            if repo.count_admins() <= 1:
                return _error(409, REASON_LAST_ADMIN)

    updated = repo.update_user(
        target_id,
        role=role,
        display_name=fields.get("display_name"),
        display_name_set=display_name_set,
    )
    if updated is None:
        return _error(404, REASON_USER_NOT_FOUND)
    return public_user(updated)


@router.delete("/admin/users/{user_id}", status_code=204, response_model=None)
@_storage_guarded
def admin_delete_user(
    user_id: str,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    actor, err = _require_admin(request, repo)
    if err is not None:
        return err
    assert actor is not None
    target_id = _parse_uuid(user_id)
    if target_id is None:
        return _error(404, REASON_USER_NOT_FOUND)
    if target_id == actor["id"]:
        return _error(409, REASON_SELF_DELETE)
    target = repo.get_user(target_id)
    if target is None:
        return _error(404, REASON_USER_NOT_FOUND)
    if target["role"] == ROLE_ADMIN and repo.count_admins() <= 1:
        return _error(409, REASON_LAST_ADMIN)
    repo.delete_user(target_id)
    return Response(status_code=204)


# Admin — places

@router.get("/admin/places", response_model=AdminPlaceListOut)
@_storage_guarded
def admin_list_places(
    request: Request,
    q: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    _, err = _require_admin(request, repo)
    if err is not None:
        return err
    rows, total = repo.list_places(
        q=q.strip(), category=category.strip(), limit=limit, offset=offset
    )
    return AdminPlaceListOut(
        items=[PlaceItem(**payload) for payload in place_payloads(rows)],
        total=total,
    )


@router.post("/admin/places", response_model=PlaceItem, status_code=201)
@_storage_guarded
def admin_create_place(
    body: AdminPlaceIn,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    _, err = _require_admin(request, repo)
    if err is not None:
        return err
    try:
        row = repo.create_place(body.model_dump(exclude_unset=True))
    except DuplicateSource:
        return _error(409, REASON_SOURCE_TAKEN)
    return PlaceItem(**place_payloads([row])[0])


@router.patch("/admin/places/{place_id}", response_model=PlaceItem)
@_storage_guarded
def admin_patch_place(
    place_id: int,
    body: AdminPlacePatch,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    _, err = _require_admin(request, repo)
    if err is not None:
        return err
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return _error(422, REASON_INVALID_REQUEST)
    try:
        row = repo.update_place(place_id, fields)
    except DuplicateSource:
        return _error(409, REASON_SOURCE_TAKEN)
    if row is None:
        return _error(404, REASON_PLACE_NOT_FOUND)
    return PlaceItem(**place_payloads([row])[0])


@router.delete("/admin/places/{place_id}", status_code=204, response_model=None)
@_storage_guarded
def admin_delete_place(
    place_id: int,
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    _, err = _require_admin(request, repo)
    if err is not None:
        return err
    if not repo.delete_place(place_id):
        return _error(404, REASON_PLACE_NOT_FOUND)
    return Response(status_code=204)


@router.get("/admin/stats", response_model=StatsOut)
@_storage_guarded
def admin_stats(
    request: Request,
    repo: AccountRepository = Depends(get_repository),
) -> Any:
    _, err = _require_admin(request, repo)
    if err is not None:
        return err
    return StatsOut(**repo.stats())
