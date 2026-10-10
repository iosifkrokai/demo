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
from db.store.accounts_store import (
    AccountRepository,
    EmailTaken,
    place_payloads,
)

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
        secure=request.url.scheme == "https",
    )


@router.post("/auth/register", response_model=PublicUser, status_code=201)
@deps.storage_guarded
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    repo: AccountRepository = Depends(deps.get_account_repository),
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
        row = repo.create_user(
            uuid.uuid4(),
            email=email,
            password_hash=hash_password(body.password),
            display_name=display_name,
            role="user",
            client_id=_optional_client_id(request),
        )
    except EmailTaken:
        return deps.error(409, REASON_EMAIL_TAKEN)
    _start_session(request, response, repo, row["id"])
    return public_user(row)


@router.post("/auth/login", response_model=PublicUser)
@deps.storage_guarded
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    email = normalize_email(body.email)
    row = repo.get_user_by_email(email) if email is not None else None
    if row is None or not verify_password(body.password, row.get("password_hash")):
        return deps.error(401, REASON_INVALID_CREDENTIALS)
    client_id = _optional_client_id(request)
    if client_id is not None:
        repo.link_client(row["id"], client_id)
    _start_session(request, response, repo, row["id"])
    return public_user(row)


@router.post("/auth/logout", status_code=204, response_model=None)
@deps.storage_guarded
def logout(
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
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
    repo: AccountRepository = Depends(deps.get_account_repository),
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
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
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
@deps.storage_guarded
def mark_visited(
    place_id: int,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    marked = repo.mark_visited(user["id"], place_id)
    place = repo.get_place(place_id) if marked is not None else None
    if marked is None or place is None:
        return deps.error(404, REASON_PLACE_NOT_FOUND)
    payload = place_payloads([place])[0]
    return VisitedItem(**payload, visited_at=marked["visited_at"])


@router.post("/me/visited", response_model=VisitedBulkOut)
@deps.storage_guarded
def mark_visited_bulk(
    body: VisitedBulkIn,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    marked = repo.mark_visited_many(user["id"], body.place_ids)
    return VisitedBulkOut(marked=marked, count=len(marked))


@router.delete("/me/visited/{place_id}", status_code=204, response_model=None)
@deps.storage_guarded
def unmark_visited(
    place_id: int,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    assert user is not None
    repo.unmark_visited(user["id"], place_id)
    return Response(status_code=204)
