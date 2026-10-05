"""``GET|PUT /api/v1/admin/settings``: the installation-wide settings, for admins only;
``user_api`` adds user management, ``invitation_api`` invitations and
``security_events_api`` the security-event log and ``workspace_api`` every Workspace's
metadata and ownership reassignment."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import SecurityEventRecorder, User
from dawam.modules.workspaces import Action
from dawam.platform.request_context import client_ip

from .internal.access import allowed_to
from .invitation_api import router as invitation_router
from .security_events_api import router as security_events_router
from .service import RegistrationSettings, SystemSettingsService
from .user_api import router as user_router
from .workspace_api import router as workspace_router

router = APIRouter(tags=["admin"])
router.include_router(user_router)
router.include_router(invitation_router)
router.include_router(security_events_router)
router.include_router(workspace_router)


SettingsManager = Annotated[User, Depends(allowed_to(Action.MANAGE_SYSTEM_SETTINGS))]
"""Declare a parameter of this type to make a route need ``MANAGE_SYSTEM_SETTINGS``."""


def system_settings(request: Request) -> SystemSettingsService:
    state = request.app.state
    events = SecurityEventRecorder(state.engine, clock=state.services.clock)
    return SystemSettingsService(state.engine, events=events)


SystemSettingsDep = Annotated[SystemSettingsService, Depends(system_settings)]


class RegistrationSettingsBody(BaseModel):
    enabled: bool = Field(description="Whether visitors may sign up on their own.")
    allowed_email_domains: list[str] = Field(
        description=(
            "Only emails at exactly one of these domains may sign up (a subdomain must be "
            "listed itself); empty allows any domain."
        ),
        max_length=1000,
    )

    @classmethod
    def of(cls, settings: RegistrationSettings) -> RegistrationSettingsBody:
        return cls(
            enabled=settings.enabled, allowed_email_domains=list(settings.allowed_email_domains)
        )


class AdminSettings(BaseModel):
    registration: RegistrationSettingsBody


class AdminSettingsUpdate(BaseModel):
    """The sections to change; each one given replaces that section, and the others are
    left as they are."""

    registration: RegistrationSettingsBody | None = None


def _current(settings: SystemSettingsService) -> AdminSettings:
    return AdminSettings(registration=RegistrationSettingsBody.of(settings.registration()))


@router.get("/admin/settings", operation_id="getAdminSettings")
def get_admin_settings(_admin: SettingsManager, settings: SystemSettingsDep) -> AdminSettings:
    """The installation-wide settings (admins only)."""
    return _current(settings)


@router.put("/admin/settings", operation_id="updateAdminSettings")
def update_admin_settings(
    body: AdminSettingsUpdate, admin: SettingsManager, settings: SystemSettingsDep, request: Request
) -> AdminSettings:
    """Change installation-wide settings (admins only); returns all of them. Each change
    is recorded as a security event."""
    if body.registration is None:
        return _current(settings)
    registration = settings.set_registration(
        enabled=body.registration.enabled,
        allowed_email_domains=body.registration.allowed_email_domains,
        actor_id=admin.id,
        ip=client_ip(request),
    )
    # The only section so far: what was saved is all there is to return.
    return AdminSettings(registration=RegistrationSettingsBody.of(registration))
