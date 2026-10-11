"""HTTP surface for accounts and visits.

The wire speaks codes, not prose; a missing identity differs from a wrong one.
The admin panel lives in ``api.routers.admin``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from api import deps
from api.models.accounts import (
    REASON_EMAIL_TAKEN,
    REASON_INVALID_CREDENTIALS,
    REASON_PLACE_NOT_FOUND,
    SESSION_COOKIE,
    AuthMeOut,
    LoginIn,
    PublicUser,
    RegisterIn,
    VisitedBulkIn,
    VisitedBulkOut,
    VisitedItem,
    VisitedListOut,
    public_user,
)
from api.models.clients import CLIENT_ID_HEADER
from core.accounts import REASON_INVALID_EMAIL, normalize_email, password_problem
from core.passwords import (
    SESSION_TTL_S,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)
from db.models.user import User
from db.store.errors import EmailTaken
from db.store.mappers import place_payload
from db.store.places import PostgresPlaceRepository
from db.store.users import UserRepository

router = APIRouter(tags=["accounts"])


def _optional_client_id(request: Request) -> uuid.UUID | None:
    """The browser's anonymous ``X-Client-Id``, when it is a well-formed UUID."""
    raw = request.headers.get(CLIENT_ID_HEADER)
    if raw is None or not raw.strip():
        return None
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError):
        return None


def _start_session(
    request: Request,
    response: Response,
    repo: UserRepository,
    user: User,
) -> None:
    """Mint a session, store only its hash, and hand the token to the browser."""
    assert user.id is not None
    token = new_session_token()
    expires = datetime.now(UTC) + timedelta(seconds=SESSION_TTL_S)
    repo.create_session(hash_token(token), user.id, expires)
    repo.touch_login(user.id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        path="/",
        secure=request.url.scheme == "https",
    )


@router.post("/auth/register", response_model=PublicUser, status_code=201)
@deps.storage_guarded
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    repo: UserRepository = Depends(deps.get_user_repository),
) -> Any:
    """Create an account (always ``role=user``) and sign it in.

    The first administrator is made by ``python -m db.seed admin``, not by registering.
    """
    email = normalize_email(body.email)
    if email is None:
        return deps.error(422, REASON_INVALID_EMAIL)
    problem = password_problem(body.password)
    if problem is not None:
        return deps.error(422, problem)
    display_name = (body.display_name or "").strip() or None
    try:
        user = repo.create_user(
            uuid.uuid4(),
            email=email,
            password_hash=hash_password(body.password),
            display_name=display_name,
            role="user",
            client_id=_optional_client_id(request),
        )
    except EmailTaken:
        return deps.error(409, REASON_EMAIL_TAKEN)
    _start_session(request, response, repo, user)
    return public_user(user)


@router.post("/auth/login", response_model=PublicUser)
@deps.storage_guarded
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    repo: UserRepository = Depends(deps.get_user_repository),
) -> Any:
    email = normalize_email(body.email)
    user = repo.get_user_by_email(email) if email is not None else None
    if user is None or not verify_password(body.password, user.password_hash):
        return deps.error(401, REASON_INVALID_CREDENTIALS)
    client_id = _optional_client_id(request)
    if client_id is not None:
        assert user.id is not None
        repo.link_client(user.id, client_id)
    _start_session(request, response, repo, user)
    return public_user(user)


@router.post("/auth/logout", status_code=204, response_model=None)
@deps.storage_guarded
def logout(
    request: Request,
    repo: UserRepository = Depends(deps.get_user_repository),
) -> Any:
    out = Response(status_code=204)
    token = deps.session_token(request)
    if token is not None:
        repo.delete_session(hash_token(token))
    out.delete_cookie(SESSION_COOKIE, path="/")
    return out


@router.get("/auth/me", response_model=AuthMeOut)
@deps.storage_guarded
def me(
    request: Request,
    repo: UserRepository = Depends(deps.get_user_repository),
) -> Any:
    """Honest about the anonymous case: ``{authenticated: false}``, not a 401.

    A 401 here would be console noise for every visitor who is not logged in.
    """
    user = deps.session_user(request, repo)
    if user is None:
        return AuthMeOut(authenticated=False, user=None)
    return AuthMeOut(authenticated=True, user=public_user(user))


@router.get("/me/visited", response_model=VisitedListOut)
@deps.storage_guarded
def list_visited(
    request: Request,
    repo: UserRepository = Depends(deps.get_user_repository),
    places: PostgresPlaceRepository = Depends(deps.get_place_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None and user.id is not None
    marks = repo.list_visited(user.id)
    by_id = {place.id: place for place in places.get_by_ids([m.place_id for m in marks])}
    items: list[VisitedItem] = []
    for mark in marks:
        place = by_id.get(mark.place_id)
        if place is None or mark.visited_at is None:
            continue
        items.append(VisitedItem(**place_payload(place), visited_at=mark.visited_at))
    return VisitedListOut(items=items, count=len(items))


@router.put("/me/visited/{place_id}", response_model=VisitedItem)
@deps.storage_guarded
def mark_visited(
    place_id: int,
    request: Request,
    repo: UserRepository = Depends(deps.get_user_repository),
    places: PostgresPlaceRepository = Depends(deps.get_place_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None and user.id is not None
    mark = repo.mark_visited(user.id, place_id)
    place = places.get_by_id(place_id) if mark is not None else None
    if mark is None or place is None or mark.visited_at is None:
        return deps.error(404, REASON_PLACE_NOT_FOUND)
    return VisitedItem(**place_payload(place), visited_at=mark.visited_at)


@router.post("/me/visited", response_model=VisitedBulkOut)
@deps.storage_guarded
def mark_visited_bulk(
    body: VisitedBulkIn,
    request: Request,
    repo: UserRepository = Depends(deps.get_user_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None and user.id is not None
    marked = repo.mark_visited_many(user.id, body.place_ids)
    return VisitedBulkOut(marked=marked, count=len(marked))


@router.delete("/me/visited/{place_id}", status_code=204, response_model=None)
@deps.storage_guarded
def unmark_visited(
    place_id: int,
    request: Request,
    repo: UserRepository = Depends(deps.get_user_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None and user.id is not None
    repo.unmark_visited(user.id, place_id)
    return Response(status_code=204)
