"""The HTTP plumbing every router shares, in one copy."""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from agent import llm
from api.models.accounts import (
    REASON_NOT_ADMIN,
    REASON_NOT_AUTHENTICATED,
    REASON_STORAGE_UNAVAILABLE,
    ROLE_ADMIN,
    SESSION_COOKIE,
)
from core.passwords import hash_token
from db.models.user import User
from db.store.errors import StorageUnavailableError
from db.store.places import PostgresPlaceRepository
from db.store.registry import Repositories
from db.store.stats import PostgresStatsRepository
from db.store.users import UserRepository

log = logging.getLogger(__name__)


def require_llm() -> None:
    """Refuse a planning request when the interpreter is not configured."""
    if not llm.chosen_provider().api_key:
        raise HTTPException(
            status_code=503,
            detail={"reason": "llm_not_configured"},
        )


def error(status: int, reason: str) -> JSONResponse:
    """A machine reason code as the whole response body."""
    return JSONResponse(status_code=status, content={"reason": reason})


def http_error(status: int, reason: str) -> HTTPException:
    """The same reason code, wrapped for a route that raises instead of returns."""
    return HTTPException(status_code=status, detail={"reason": reason})


def storage_guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn a storage outage into ``503 storage_unavailable``, never a 500."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except StorageUnavailableError as exc:
            log.warning("storage unavailable: %s", exc)
            return error(503, REASON_STORAGE_UNAVAILABLE)

    return wrapper


def parse_uuid(value: str) -> uuid.UUID | None:
    """A UUID, or ``None`` for anything that is not one."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def session_token(request: Request) -> str | None:
    """The session token from the cookie, or a ``Bearer`` header."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie and cookie.strip():
        return cookie.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return None


def session_user(request: Request, repo: UserRepository) -> User | None:
    token = session_token(request)
    if token is None:
        return None
    return repo.get_session_user(hash_token(token))


def require_user(request: Request, repo: UserRepository) -> tuple[User | None, JSONResponse | None]:
    user = session_user(request, repo)
    if user is None:
        return None, error(401, REASON_NOT_AUTHENTICATED)
    return user, None


def require_admin(
    request: Request, repo: UserRepository
) -> tuple[User | None, JSONResponse | None]:
    user, err = require_user(request, repo)
    if err is not None:
        return None, err
    if user is None:
        return None, error(401, REASON_NOT_AUTHENTICATED)
    if user.role != ROLE_ADMIN:
        return None, error(403, REASON_NOT_ADMIN)
    return user, None


def get_repos(request: Request) -> Repositories:
    """The repositories the lifespan built onto ``app.state.repos``."""
    return request.app.state.repos


def get_place_repository(request: Request) -> PostgresPlaceRepository:
    """The process-wide place repository."""
    return get_repos(request).places


def get_user_repository(request: Request) -> UserRepository:
    """The process-wide account repository."""
    return get_repos(request).users


def get_stats_repository(request: Request) -> PostgresStatsRepository:
    """The process-wide stats repository."""
    return get_repos(request).stats
