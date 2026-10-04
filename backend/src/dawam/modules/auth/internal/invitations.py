"""Invitations (spec stories 10, 15; §6.1): joining DAWAM by an emailed link while
self-registration is off."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.platform.email import Delivery, EmailMessage, Mailer, OneTimeLink
from dawam.platform.errors import ApiError
from dawam.platform.pagination import decode_cursor, encode_cursor

from ..service import (
    SignedIn,
    _email_must_be_free,
    _new_user_record,
    _start_session,
    _token_hash,
)
from ..tables import InvitationRecord, UserRecord
from .credentials import is_valid_email, normalize_email
from .security_events import SecurityEventRecorder

logger = logging.getLogger(__name__)

INVITATION_LIFETIME = timedelta(days=7)
LINK_PURPOSE = "invitation"
"""The ``OneTimeLink.purpose`` of an invitation link kept for an admin to share."""

InvitedRole = Literal["owner", "editor", "viewer"]
"""A Workspace role an invitation may carry (the ``workspaces`` module's roles; auth
cannot import them)."""


class InvitedMembership(Protocol):
    """Joins an invited user to the Workspace their invitation carries. Auth cannot
    import the ``workspaces`` module, which implements this; the composition root hands
    it over as ``app.state.invited_membership``."""

    def join(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        role: InvitedRole,
        invited_by: uuid.UUID,
    ) -> bool:
        """Make ``user_id`` a member as ``role`` inside ``db``'s transaction, the one that
        accepts the invitation, so the account and its membership commit together.
        Returns False, adding nobody, if the invitation no longer holds (its sender is
        no longer an owner there)."""
        ...


@dataclass(frozen=True)
class Inviter:
    id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class Invitation:
    """A pending invitation, as admins see it (never its token)."""

    id: uuid.UUID
    email: str
    invited_by: Inviter
    workspace_id: uuid.UUID | None
    workspace_role: InvitedRole | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class SentInvitation:
    invitation: Invitation
    delivery: Delivery
    """How the link went out: emailed, or kept for an admin to share."""


@dataclass(frozen=True)
class InvitationPage:
    items: list[Invitation]
    next_cursor: str | None
    """Pass it as ``cursor`` for the next page; None on the last."""


@dataclass(frozen=True)
class InvitationLink:
    """What the invitee's page may know before accepting: who the link is for."""

    email: str
    expires_at: datetime


def _invalid_invitation() -> ApiError:
    # One error for unknown, used, revoked and expired links, so none can be told apart.
    return ApiError(
        400,
        "invalid_invitation",
        "This invitation link is invalid, used, revoked or expired. Ask an admin for a new one.",
    )


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "There is no pending invitation with this id.")


class Invitations:
    """Inviting people by email, and accepting an invitation (spec §6.1): a single-use
    link, valid 7 days, emailed through the ``Mailer`` (kept for an admin to share
    without SMTP). The link carries a random token; the ``invitations`` row stores only
    its SHA-256, and the account it creates gets the invitation's email.

    Callers authorize the admin side first (``can`` with ``Action.MANAGE_USERS``); every
    change is recorded as a security event."""

    def __init__(
        self,
        engine: sa.Engine,
        settings: Settings,
        *,
        mailer: Mailer,
        clock: Clock,
        membership: InvitedMembership | None = None,
    ) -> None:
        """``membership`` is needed to accept an invitation into a Workspace."""
        self._engine = engine
        self._mailer = mailer
        self._membership = membership
        self._clock = clock
        self._public_url = settings.public_url
        self._session_lifetime = timedelta(days=settings.session_absolute_timeout_days)
        self._events = SecurityEventRecorder(engine, clock=clock)

    def invite(
        self,
        email: str,
        *,
        actor_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        workspace_role: InvitedRole | None = None,
        workspace_name: str | None = None,
        ip: str | None = None,
    ) -> SentInvitation:
        """Invite ``email`` as ``actor_id`` from ``ip``, optionally into a Workspace as
        ``workspace_role`` (its name, ``workspace_name``, goes into the email), and send
        the link. A pending invitation to the same email is
        replaced: its link stops working. Records an ``invitation_created`` security event.

        Raises ``ApiError`` 422 ``invalid_email``, 409 ``email_taken`` (the email has an
        account already)."""
        email = normalize_email(email)
        if not is_valid_email(email):
            raise ApiError(422, "invalid_email", "The email address is not valid.")
        if (workspace_id is None) != (workspace_role is None):
            raise ValueError("an invitation carries a Workspace and a role, or neither")
        now = self._clock()
        token = secrets.token_urlsafe(32)
        with Session(self._engine) as db, db.begin():
            # One invitation of an email at a time: concurrent invites queue here.
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(f"inv:{email}"))))
            if db.scalar(sa.select(sa.exists().where(UserRecord.email == email))):
                raise ApiError(
                    409, "email_taken", f"{email} already has an account: they can sign in."
                )
            inviter = db.get(UserRecord, actor_id)
            if inviter is None:
                raise ValueError(f"the inviting user {actor_id} does not exist")
            replaced = db.execute(
                sa.update(InvitationRecord)
                .where(InvitationRecord.email == email, *self._pending(now))
                .values(revoked_at=now)
            ).rowcount
            record = InvitationRecord(
                email=email,
                token_hash=_token_hash(token),
                invited_by=actor_id,
                workspace_id=workspace_id,
                workspace_role=workspace_role,
                created_at=now,
                expires_at=now + INVITATION_LIFETIME,
            )
            db.add(record)
            db.flush()
            self._events.record(
                "invitation_created",
                actor_id=actor_id,
                target_type="invitation",
                target_id=record.id,
                metadata={"email": email},
                ip=ip,
                db=db,
            )
            invitation = _invitation(record, inviter)
        if replaced:
            self._mailer.withdraw_links(recipient=email, purpose=LINK_PURPOSE)
        delivery = self._send_link(invitation, token, workspace_name)
        logger.info("invitation sent", extra={"invitation_id": str(invitation.id)})
        return SentInvitation(invitation=invitation, delivery=delivery)

    def pending(self, *, limit: int, cursor: str | None = None) -> InvitationPage:
        """Invitations not yet accepted, revoked or expired, by email, ``limit`` at a
        time. Raises ``ApiError`` 422 ``invalid_cursor``."""
        statement = (
            sa.select(InvitationRecord, UserRecord)
            .join(UserRecord, UserRecord.id == InvitationRecord.invited_by)
            .where(*self._pending(self._clock()))
            .order_by(InvitationRecord.email)
            .limit(limit + 1)
        )
        if cursor is not None:
            # At most one invitation of an email is pending, so the email is a key.
            (after,) = decode_cursor(cursor, 1)
            statement = statement.where(InvitationRecord.email > after)
        with Session(self._engine) as db:
            rows = db.execute(statement).all()
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1][0].email) if len(rows) > limit else None
        return InvitationPage(
            items=[_invitation(record, inviter) for record, inviter in page],
            next_cursor=next_cursor,
        )

    def revoke(
        self, invitation_id: uuid.UUID, *, actor_id: uuid.UUID, ip: str | None = None
    ) -> None:
        """Make a pending invitation's link stop working, as ``actor_id`` from ``ip``.
        Records an ``invitation_revoked`` security event.

        Raises ``ApiError`` 404 ``not_found`` if it is not pending (any more)."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            record = db.scalar(
                sa.select(InvitationRecord)
                .where(InvitationRecord.id == invitation_id, *self._pending(now))
                .with_for_update()
            )
            if record is None:
                raise _not_found()
            record.revoked_at = now
            self._events.record(
                "invitation_revoked",
                actor_id=actor_id,
                target_type="invitation",
                target_id=record.id,
                metadata={"email": record.email},
                ip=ip,
                db=db,
            )
            email = record.email
        self._mailer.withdraw_links(recipient=email, purpose=LINK_PURPOSE)

    def look_up(self, token: str) -> InvitationLink:
        """Who a still-usable invitation link is for. Raises ``ApiError`` 400
        ``invalid_invitation``."""
        with Session(self._engine) as db:
            record = db.scalar(
                sa.select(InvitationRecord).where(
                    InvitationRecord.token_hash == _token_hash(token),
                    *self._pending(self._clock()),
                )
            )
        if record is None:
            raise _invalid_invitation()
        return InvitationLink(email=record.email, expires_at=record.expires_at)

    def accept(
        self,
        token: str,
        *,
        display_name: str,
        password: str,
        replacing: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SignedIn:
        """Create the invited user (a regular user, with the invitation's email) with
        ``display_name`` and ``password``, and sign them in; the link stops working.
        ``replacing``, ``ip`` and ``user_agent`` are as for ``AuthService.sign_in``.
        Records an ``invitation_accepted`` security event.

        Raises ``ApiError``: 400 ``invalid_invitation`` (unknown, used, revoked or
        expired), 422 ``invalid_display_name`` or ``invalid_password`` (the invitation
        stays usable), 409 ``email_taken`` (the email got an account meanwhile)."""
        now = self._clock()
        with _email_must_be_free(), Session(self._engine) as db, db.begin():
            invitation = db.scalar(
                sa.select(InvitationRecord)
                .where(InvitationRecord.token_hash == _token_hash(token), *self._pending(now))
                .with_for_update()
            )
            if invitation is None:
                raise _invalid_invitation()
            user = _new_user_record(invitation.email, password, display_name, "user", now=now)
            db.add(user)
            db.flush()
            invitation.accepted_at = now
            if invitation.workspace_id is not None:
                if self._membership is None:
                    raise RuntimeError("accepting a Workspace invitation needs the membership")
                joined = self._membership.join(
                    db,
                    workspace_id=invitation.workspace_id,
                    user_id=user.id,
                    role=invitation.workspace_role,  # type: ignore[arg-type]  # a check constraint
                    invited_by=invitation.invited_by,
                )
                if not joined:
                    raise _invalid_invitation()  # rolls back the new account too
            self._events.record(
                "invitation_accepted",
                actor_id=user.id,
                target_type="user",
                target_id=user.id,
                metadata={
                    "email": user.email,
                    "invitation_id": str(invitation.id),
                    "invited_by": str(invitation.invited_by),
                },
                ip=ip,
                db=db,
            )
            signed_in = _start_session(
                db,
                user,
                password,
                now,
                lifetime=self._session_lifetime,
                replacing=replacing,
                ip=ip,
                user_agent=user_agent,
            )
            email = user.email
        self._mailer.withdraw_links(recipient=email, purpose=LINK_PURPOSE)
        logger.info("invitation accepted", extra={"user_id": str(signed_in.user.id)})
        return signed_in

    @staticmethod
    def _pending(now: datetime) -> tuple[sa.ColumnElement[bool], ...]:
        return (
            InvitationRecord.accepted_at.is_(None),
            InvitationRecord.revoked_at.is_(None),
            InvitationRecord.expires_at > now,
        )

    def _send_link(
        self, invitation: Invitation, token: str, workspace_name: str | None
    ) -> Delivery:
        url = f"{self._public_url}/accept-invitation#token={token}"
        days = INVITATION_LIFETIME.days
        to_join = "DAWAM"
        if workspace_name is not None:
            to_join = f'the Workspace "{workspace_name}" in DAWAM'
        return self._mailer.send(
            EmailMessage(
                to=invitation.email,
                subject="You are invited to DAWAM",
                body=(
                    f"{invitation.invited_by.display_name} has invited you to join {to_join}.\n\n"
                    f"To accept, open this link within {days} days and choose a display "
                    f"name and a password:\n\n"
                    f"{url}\n\n"
                    "The link works once. If you were not expecting it, ignore this email.\n"
                ),
            ),
            link=OneTimeLink(url=url, purpose=LINK_PURPOSE, expires_at=invitation.expires_at),
        )


def _invitation(record: InvitationRecord, inviter: UserRecord) -> Invitation:
    return Invitation(
        id=record.id,
        email=record.email,
        invited_by=Inviter(id=inviter.id, display_name=inviter.display_name),
        workspace_id=record.workspace_id,
        workspace_role=record.workspace_role,  # type: ignore[arg-type]  # a check constraint
        created_at=record.created_at,
        expires_at=record.expires_at,
    )
