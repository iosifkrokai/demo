"""The HTTP plumbing every router shares, in one copy.

Three kinds of helper live here: the request guards that map a condition or an
error onto HTTP, the session/admin gates, and the accessors that read the app's
resources off ``request.app.state``. Routers import these instead of each
carrying a private copy.

The wire speaks codes, not prose. ``error`` returns ``{"reason": code}`` as the
whole body; ``http_error`` is the same code as a raised ``HTTPException``.
"""

from __future__ import annotations

import functools
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from api.models.accounts import (
    REASON_NOT_ADMIN,
    REASON_NOT_AUTHENTICATED,
    REASON_STORAGE_UNAVAILABLE,
    ROLE_ADMIN,
    SESSION_COOKIE,
)
from core.config import openrouter_api_key
from core.errors import AgentError
from core.passwords import hash_token
from db.store.accounts_store import (
    AccountRepository,
    PostgresAccountRepository,
    StorageUnavailable as AccountStorageUnavailable,
)
from db.store.clients_store import (
    ClientRepository,
    PostgresClientRepository,
    StorageUnavailable as ClientStorageUnavailable,
)
from db.store.errors import StorageUnavailable as StoreStorageUnavailable
from db.store.registry import Repositories
from planner.pipeline import Pipeline

log = logging.getLogger(__name__)

# The stores raised three separate `StorageUnavailable` classes; a shared guard
# has to catch all three or it would let one store's outage escape as a 500.
_STORAGE_UNAVAILABLE = (
    StoreStorageUnavailable,
    AccountStorageUnavailable,
    ClientStorageUnavailable,
)

_default_account_repo_lock = threading.Lock()
_default_client_repo_lock = threading.Lock()


# --- request guards ---------------------------------------------------------


def require_llm() -> None:
    """Refuse a planning request when the interpreter is not configured.

    Reading a free-text query is the model's job; without a key there is no
    reader. Answering anyway — with a keyword parse — would silently give the
    tourist a worse route and no way to tell, so the request is refused instead.
    """
    if not openrouter_api_key():
        raise HTTPException(
            status_code=503,
            detail={"reason": "llm_not_configured"},
        )


def call_planner[T](fn: Callable[..., T], **kwargs: Any) -> T:
    """Run a planner call and map its failures onto HTTP.

    AgentError carries the planner's status; any other failure stays a 500.
    """
    try:
        return fn(**kwargs)
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e)) from e


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
        except _STORAGE_UNAVAILABLE as exc:
            log.warning("storage unavailable: %s", exc)
            return error(503, REASON_STORAGE_UNAVAILABLE)

    return wrapper


def parse_uuid(value: str) -> uuid.UUID | None:
    """A UUID, or ``None`` for anything that is not one."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


# --- session and admin gates ------------------------------------------------


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


def session_user(request: Request, repo: AccountRepository) -> dict[str, Any] | None:
    token = session_token(request)
    if token is None:
        return None
    return repo.get_session_user(hash_token(token))


def require_user(
    request: Request, repo: AccountRepository
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    user = session_user(request, repo)
    if user is None:
        return None, error(401, REASON_NOT_AUTHENTICATED)
    return user, None


def require_admin(
    request: Request, repo: AccountRepository
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    user, err = require_user(request, repo)
    if err is not None:
        return None, err
    assert user is not None
    if user["role"] != ROLE_ADMIN:
        return None, error(403, REASON_NOT_ADMIN)
    return user, None


# --- resource accessors -----------------------------------------------------


def get_planner(request: Request) -> Pipeline:
    """The pipeline the lifespan built onto ``app.state.planner``."""
    return request.app.state.planner


def get_repos(request: Request) -> Repositories:
    """The repositories the lifespan built onto ``app.state.repos``."""
    return request.app.state.repos


def get_account_repository(request: Request) -> AccountRepository:
    """The process-wide account repository, or a test-injected fake.

    Tests set ``app.state.accounts_repository``.
    """
    repo = getattr(request.app.state, "accounts_repository", None)
    if repo is None:
        with _default_account_repo_lock:
            repo = getattr(request.app.state, "accounts_repository", None)
            if repo is None:
                repo = PostgresAccountRepository()
                request.app.state.accounts_repository = repo
    return repo


def get_client_repository(request: Request) -> ClientRepository:
    """The process-wide client repository, or a test-injected fake.

    Tests set ``app.state.clients_repository``.
    """
    repo = getattr(request.app.state, "clients_repository", None)
    if repo is None:
        with _default_client_repo_lock:
            repo = getattr(request.app.state, "clients_repository", None)
            if repo is None:
                repo = PostgresClientRepository()
                request.app.state.clients_repository = repo
    return repo
