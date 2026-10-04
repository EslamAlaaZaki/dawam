"""``POST /api/v1/admin/users/invite`` and ``/api/v1/admin/invitations``: inviting people
by email and the pending invitations, for admins only (spec stories 10, 15)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from dawam.modules.auth import MAX_EMAIL_LENGTH, Invitation, Invitations, InvitedRole, User
from dawam.modules.workspaces import Action
from dawam.platform.email import Delivery
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit
from dawam.platform.request_context import client_ip

from .internal.access import allowed_to

router = APIRouter(tags=["admin"])


UserManager = Annotated[User, Depends(allowed_to(Action.MANAGE_USERS))]


def invitations(request: Request) -> Invitations:
    state = request.app.state
    return Invitations(
        state.engine, state.settings, mailer=state.mailer, clock=state.services.clock
    )


InvitationsDep = Annotated[Invitations, Depends(invitations)]


class InviterOut(BaseModel):
    id: uuid.UUID
    display_name: str


class PendingInvitation(BaseModel):
    id: uuid.UUID
    email: str
    invited_by: InviterOut
    workspace_id: uuid.UUID | None = Field(
        description="The Workspace the invitee joins on accepting, if any."
    )
    workspace_role: InvitedRole | None = Field(description="Their role in that Workspace.")
    created_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, invitation: Invitation) -> PendingInvitation:
        return cls(
            id=invitation.id,
            email=invitation.email,
            invited_by=InviterOut(
                id=invitation.invited_by.id, display_name=invitation.invited_by.display_name
            ),
            workspace_id=invitation.workspace_id,
            workspace_role=invitation.workspace_role,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
        )


class PendingInvitationPage(BaseModel):
    items: list[PendingInvitation]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


class InviteRequest(BaseModel):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)


class SentInvitationOut(BaseModel):
    invitation: PendingInvitation
    delivery: Delivery = Field(
        description="`sent`: the invitation was emailed; `link_for_admin`: it could not be, "
        "so its link waits in the undelivered links for you to share."
    )


@router.post("/admin/users/invite", operation_id="inviteUser", status_code=201)
def invite_user(
    body: InviteRequest, admin: UserManager, invitations: InvitationsDep, request: Request
) -> SentInvitationOut:
    """Invite someone by email (admins only): they get a link, valid 7 days, to choose a
    display name and password and join, even while self-registration is off. An email
    that has an account already is refused (409 ``email_taken``); a pending invitation
    to the same email is replaced. Recorded as a security event."""
    sent = invitations.invite(body.email, actor_id=admin.id, ip=client_ip(request))
    return SentInvitationOut(
        invitation=PendingInvitation.of(sent.invitation), delivery=sent.delivery
    )


@router.get("/admin/invitations", operation_id="listInvitations")
def list_invitations(
    _admin: UserManager,
    invitations: InvitationsDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> PendingInvitationPage:
    """Invitations not yet accepted, revoked or expired, by email (admins only)."""
    page = invitations.pending(limit=limit, cursor=cursor)
    return PendingInvitationPage(
        items=[PendingInvitation.of(invitation) for invitation in page.items],
        next_cursor=page.next_cursor,
    )


@router.delete(
    "/admin/invitations/{invitation_id}",
    operation_id="revokeInvitation",
    status_code=204,
    response_class=Response,
)
def revoke_invitation(
    invitation_id: uuid.UUID, admin: UserManager, invitations: InvitationsDep, request: Request
) -> Response:
    """Revoke a pending invitation (admins only): its link stops working. Recorded as a
    security event."""
    invitations.revoke(invitation_id, actor_id=admin.id, ip=client_ip(request))
    return Response(status_code=204)
