"""HTTP surface for the admin panel.

The wire speaks codes, not prose; only an administrator gets past the guard.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from api import deps
from api.models.accounts import (
    REASON_INVALID_REQUEST,
    REASON_LAST_ADMIN,
    REASON_PLACE_NOT_FOUND,
    REASON_SELF_DELETE,
    REASON_SELF_ROLE,
    REASON_SOURCE_TAKEN,
    REASON_USER_NOT_FOUND,
    ROLE_ADMIN,
    AdminPlaceIn,
    AdminPlaceListOut,
    AdminPlacePatch,
    AdminUserItem,
    AdminUserListOut,
    AdminUserPatch,
    PlaceItem,
    PublicUser,
    StatsOut,
    public_user,
)
from db.store.accounts_store import (
    MAX_LIST_LIMIT,
    AccountRepository,
    DuplicateSource,
    place_payloads,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=AdminUserListOut)
@deps.storage_guarded
def admin_list_users(
    request: Request,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    _, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    rows, total = repo.list_users(q=q.strip(), limit=limit, offset=offset)
    return AdminUserListOut(
        items=[AdminUserItem(**row) for row in rows], total=total
    )


@router.patch("/users/{user_id}", response_model=PublicUser)
@deps.storage_guarded
def admin_patch_user(
    user_id: str,
    body: AdminUserPatch,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    actor, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    assert actor is not None
    target_id = deps.parse_uuid(user_id)
    if target_id is None:
        return deps.error(404, REASON_USER_NOT_FOUND)
    target = repo.get_user(target_id)
    if target is None:
        return deps.error(404, REASON_USER_NOT_FOUND)

    fields = body.model_dump(exclude_unset=True)
    role = fields.get("role")
    display_name_set = "display_name" in fields

    if role is not None and role != target["role"]:
        if target_id == actor["id"]:
            return deps.error(409, REASON_SELF_ROLE)
        if target["role"] == ROLE_ADMIN and role != ROLE_ADMIN:
            if repo.count_admins() <= 1:
                return deps.error(409, REASON_LAST_ADMIN)

    updated = repo.update_user(
        target_id,
        role=role,
        display_name=fields.get("display_name"),
        display_name_set=display_name_set,
    )
    if updated is None:
        return deps.error(404, REASON_USER_NOT_FOUND)
    return public_user(updated)


@router.delete("/users/{user_id}", status_code=204, response_model=None)
@deps.storage_guarded
def admin_delete_user(
    user_id: str,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    actor, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    assert actor is not None
    target_id = deps.parse_uuid(user_id)
    if target_id is None:
        return deps.error(404, REASON_USER_NOT_FOUND)
    if target_id == actor["id"]:
        return deps.error(409, REASON_SELF_DELETE)
    target = repo.get_user(target_id)
    if target is None:
        return deps.error(404, REASON_USER_NOT_FOUND)
    if target["role"] == ROLE_ADMIN and repo.count_admins() <= 1:
        return deps.error(409, REASON_LAST_ADMIN)
    repo.delete_user(target_id)
    return Response(status_code=204)


@router.get("/places", response_model=AdminPlaceListOut)
@deps.storage_guarded
def admin_list_places(
    request: Request,
    q: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    _, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    rows, total = repo.list_places(
        q=q.strip(), category=category.strip(), limit=limit, offset=offset
    )
    return AdminPlaceListOut(
        items=[PlaceItem(**payload) for payload in place_payloads(rows)],
        total=total,
    )


@router.post("/places", response_model=PlaceItem, status_code=201)
@deps.storage_guarded
def admin_create_place(
    body: AdminPlaceIn,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    _, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    try:
        row = repo.create_place(body.model_dump(exclude_unset=True))
    except DuplicateSource:
        return deps.error(409, REASON_SOURCE_TAKEN)
    return PlaceItem(**place_payloads([row])[0])


@router.patch("/places/{place_id}", response_model=PlaceItem)
@deps.storage_guarded
def admin_patch_place(
    place_id: int,
    body: AdminPlacePatch,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    _, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return deps.error(422, REASON_INVALID_REQUEST)
    try:
        row = repo.update_place(place_id, fields)
    except DuplicateSource:
        return deps.error(409, REASON_SOURCE_TAKEN)
    if row is None:
        return deps.error(404, REASON_PLACE_NOT_FOUND)
    return PlaceItem(**place_payloads([row])[0])


@router.delete("/places/{place_id}", status_code=204, response_model=None)
@deps.storage_guarded
def admin_delete_place(
    place_id: int,
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    _, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    if not repo.delete_place(place_id):
        return deps.error(404, REASON_PLACE_NOT_FOUND)
    return Response(status_code=204)


@router.get("/stats", response_model=StatsOut)
@deps.storage_guarded
def admin_stats(
    request: Request,
    repo: AccountRepository = Depends(deps.get_account_repository),
) -> Any:
    _, err = deps.require_admin(request, repo)
    if err is not None:
        return err
    return StatsOut(**repo.stats())
