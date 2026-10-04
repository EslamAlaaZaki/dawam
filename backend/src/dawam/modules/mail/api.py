"""Admin endpoints: ``/api/v1/admin/smtp`` (SMTP settings and a test send) and
``/api/v1/admin/undelivered-links`` (links DAWAM could not email, for an admin to copy).

The SMTP password goes in only: responses say ``has_password`` instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser, User
from dawam.modules.workspaces import INSTALLATION, Action, can
from dawam.platform.email import SmtpSecurity
from dawam.platform.errors import ApiError
from dawam.platform.request_context import client_ip

from .service import KEEP_PASSWORD, MailService, UndeliveredReason

router = APIRouter(tags=["admin"])


def mail_service(request: Request) -> MailService:
    state = request.app.state
    return MailService(
        state.engine, state.settings, sender=state.services.email, clock=state.services.clock
    )


MailServiceDep = Annotated[MailService, Depends(mail_service)]


def email_admin(user: CurrentUser) -> User:
    """The signed-in user if the policy lets them manage email (admins); others get
    ``403 forbidden``."""
    if not can(user, Action.MANAGE_EMAIL, INSTALLATION):
        raise ApiError(403, "forbidden", "Only an admin can manage email.")
    return user


EmailAdmin = Annotated[User, Depends(email_admin)]


class SmtpSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    host: str
    port: int
    security: SmtpSecurity
    username: str | None
    has_password: bool
    sender: str
    updated_at: datetime


class SmtpSettingsIn(BaseModel):
    host: str = Field(max_length=255)
    port: int = Field(ge=1, le=65535)
    security: SmtpSecurity = Field(
        description="`none`: plain SMTP; `starttls`: upgrade after connecting (usually port "
        "587); `tls`: TLS from the start (usually 465). Certificates are verified."
    )
    username: str | None = Field(
        default=None, max_length=255, description="Leave empty for a server without sign-in."
    )
    password: str | None = Field(
        default=None,
        max_length=1024,
        description="Leave out to keep the saved password; `null` removes it. Changing the "
        "host, port or username needs it entered again.",
    )
    sender: str = Field(max_length=320, description="The From address.")


class TestEmailIn(BaseModel):
    to: str | None = Field(
        default=None, max_length=320, description="Defaults to the signed-in admin."
    )


class TestEmailOut(BaseModel):
    to: str


class UndeliveredLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipient: str
    subject: str
    purpose: str = Field(description="What the link is for, e.g. `password_reset`.")
    url: str
    reason: UndeliveredReason
    created_at: datetime
    expires_at: datetime


class UndeliveredLinksOut(BaseModel):
    items: list[UndeliveredLinkOut]


@router.get("/admin/smtp", operation_id="getSmtpSettings")
def get_smtp_settings(admin: EmailAdmin, mail: MailServiceDep) -> SmtpSettingsOut | None:
    """The saved SMTP settings, or `null` while SMTP is off."""
    settings = mail.smtp_settings()
    return SmtpSettingsOut.model_validate(settings) if settings else None


@router.put("/admin/smtp", operation_id="saveSmtpSettings")
def save_smtp_settings(
    body: SmtpSettingsIn, request: Request, admin: EmailAdmin, mail: MailServiceDep
) -> SmtpSettingsOut:
    """Save the SMTP settings; DAWAM emails links from now on."""
    settings = mail.save_smtp_settings(
        host=body.host,
        port=body.port,
        security=body.security,
        sender=body.sender,
        username=body.username,
        password=body.password if "password" in body.model_fields_set else KEEP_PASSWORD,
        by=admin.id,
        ip=client_ip(request),
    )
    return SmtpSettingsOut.model_validate(settings)


@router.delete(
    "/admin/smtp", operation_id="clearSmtpSettings", status_code=204, response_class=Response
)
def clear_smtp_settings(request: Request, admin: EmailAdmin, mail: MailServiceDep) -> Response:
    """Turn SMTP off: links are then kept for admins to copy."""
    mail.clear_smtp_settings(by=admin.id, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/admin/smtp/test", operation_id="sendTestEmail")
def send_test_email(body: TestEmailIn, admin: EmailAdmin, mail: MailServiceDep) -> TestEmailOut:
    """Send a test email through the saved settings."""
    to = body.to or admin.email
    mail.send_test(to)
    return TestEmailOut(to=to.strip())


@router.get("/admin/undelivered-links", operation_id="listUndeliveredLinks")
def list_undelivered_links(admin: EmailAdmin, mail: MailServiceDep) -> UndeliveredLinksOut:
    """Links DAWAM could not email that still work, newest first, for an admin to share."""
    return UndeliveredLinksOut(
        items=[UndeliveredLinkOut.model_validate(link) for link in mail.undelivered_links()]
    )


@router.delete(
    "/admin/undelivered-links/{link_id}",
    operation_id="dismissUndeliveredLink",
    status_code=204,
    response_class=Response,
)
def dismiss_undelivered_link(
    link_id: uuid.UUID, admin: EmailAdmin, mail: MailServiceDep
) -> Response:
    """Remove a link from the list (it keeps working until it expires or is used)."""
    mail.dismiss_link(link_id)
    return Response(status_code=204)
