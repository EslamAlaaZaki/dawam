"""``/api/v1/admin/users``: user management, for admins only (spec stories 14-19)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    PasswordResets,
    SystemRole,
    User,
    UserAdministration,
)
from dawam.modules.workspaces import Action
from dawam.platform.email import Delivery
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit
from dawam.platform.request_context import client_ip

from .internal.access import allowed_to

router = APIRouter(tags=["admin"])


UserManager = Annotated[User, Depends(allowed_to(Action.MANAGE_USERS))]


def user_administration(request: Request) -> UserAdministration:
    state = request.app.state
    return UserAdministration(state.engine, clock=state.services.clock)


UserAdministrationDep = Annotated[UserAdministration, Depends(user_administration)]


class AdminUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    system_role: SystemRole
    is_active: bool = Field(description="False once deactivated: they cannot sign in.")
    must_change_password: bool = Field(
        description="Created with a temporary password they have not changed yet."
    )
    created_at: datetime
    last_login_at: datetime | None


class AdminUserPage(BaseModel):
    items: list[AdminUser]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


@router.get("/admin/users", operation_id="listUsers")
def list_users(
    _admin: UserManager,
    users: UserAdministrationDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
    q: Annotated[
        str | None,
        Query(max_length=320, description="Only users whose email or display name contains it."),
    ] = None,
    role: Annotated[SystemRole | None, Query(description="Only users with this role.")] = None,
    active: Annotated[
        bool | None, Query(description="Only active (true) or deactivated (false) users.")
    ] = None,
) -> AdminUserPage:
    """Users by email, optionally searched and filtered (admins only)."""
    page = users.list_users(
        limit=limit, cursor=cursor, search=q, system_role=role, is_active=active
    )
    return AdminUserPage(
        items=[AdminUser.model_validate(user) for user in page.items], next_cursor=page.next_cursor
    )


class UpdateUserRequest(BaseModel):
    """The changes to make; a field left out stays as it is."""

    system_role: SystemRole | None = Field(
        default=None, description="Promote to `admin` or demote to `user`."
    )
    is_active: bool | None = Field(
        default=None,
        description="False deactivates the user (their sessions end at once); true "
        "reactivates them.",
    )


@router.patch("/admin/users/{user_id}", operation_id="updateUser")
def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    admin: UserManager,
    users: UserAdministrationDep,
    request: Request,
) -> AdminUser:
    """Promote or demote, deactivate or reactivate a user (admins only). The last active
    admin can be neither demoted nor deactivated (409 ``last_admin``). Each change is
    recorded as a security event."""
    user = users.update_user(
        user_id,
        system_role=body.system_role,
        is_active=body.is_active,
        actor_id=admin.id,
        ip=client_ip(request),
    )
    return AdminUser.model_validate(user)


def password_resets(request: Request) -> PasswordResets:
    state = request.app.state
    return PasswordResets(
        state.engine, state.settings, mailer=state.mailer, clock=state.services.clock
    )


PasswordResetsDep = Annotated[PasswordResets, Depends(password_resets)]


class ForcedReset(BaseModel):
    delivery: Delivery = Field(
        description="`sent`: the reset link was emailed; `link_for_admin`: it could not be, "
        "so it waits in the undelivered links for you to share."
    )


@router.post("/admin/users/{user_id}/force-reset", operation_id="forcePasswordReset")
def force_password_reset(
    user_id: uuid.UUID, admin: UserManager, resets: PasswordResetsDep, request: Request
) -> ForcedReset:
    """Answer a suspected compromise (admins only): every session of the user ends, their
    password stops working, and they get a reset link. A deactivated user's cannot be
    reset (409 ``user_deactivated``). Recorded as a security event."""
    delivery = resets.force_reset(user_id, actor_id=admin.id, ip=client_ip(request))
    return ForcedReset(delivery=delivery)


class CreateUserRequest(BaseModel):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    display_name: str = Field(max_length=1000)
    system_role: SystemRole = "user"
    temporary_password: str = Field(
        max_length=MAX_PASSWORD_LENGTH,
        description="Meets the password policy; the user must change it at first sign-in.",
    )


@router.post("/admin/users", operation_id="createUser", status_code=201)
def create_user(
    body: CreateUserRequest, admin: UserManager, users: UserAdministrationDep, request: Request
) -> AdminUser:
    """Create a user with a temporary password (admins only). Until they change it, the
    user can do nothing but read ``/me``, change the password and sign out. Recorded as a
    security event."""
    user = users.create_user(
        email=body.email,
        display_name=body.display_name,
        system_role=body.system_role,
        temporary_password=body.temporary_password,
        actor_id=admin.id,
        ip=client_ip(request),
    )
    return AdminUser.model_validate(user)
