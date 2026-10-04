"""``GET|PUT /api/v1/admin/settings``: the installation-wide settings, for admins only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, SecurityEventRecorder, User
from dawam.modules.workspaces import INSTALLATION, Action, can
from dawam.platform.errors import ApiError

from .service import RegistrationSettings, SystemSettingsService

router = APIRouter(tags=["admin"])


def settings_manager(user: CurrentUser) -> User:
    """The signed-in user if the central policy lets them manage the system settings
    (admins); anyone else gets ``403 forbidden`` (anonymous ``401 unauthenticated``)."""
    if not can(user, Action.MANAGE_SYSTEM_SETTINGS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


SettingsManager = Annotated[User, Depends(settings_manager)]
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
    if body.registration is not None:
        settings.set_registration(
            enabled=body.registration.enabled,
            allowed_email_domains=body.registration.allowed_email_domains,
            actor_id=admin.id,
            ip=request.client.host if request.client else None,
        )
    return _current(settings)
