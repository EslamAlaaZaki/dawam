"""Workspace membership (spec stories 30-34, §6.2): adding people by email, inviting
those without an account, changing roles, removing members, leaving and transferring
ownership.

A Workspace never ends up without an owner. Every change takes the Workspace row
``FOR UPDATE`` first, so concurrent changes queue and the owner count read here stays
true until commit; the deferred ``workspace_has_owner`` trigger backs it up.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import (
    AuthService,
    Invitation,
    Invitations,
    InvitedRole,
    RegistrationPolicy,
    User,
)
from dawam.platform.clock import Clock
from dawam.platform.email import Delivery
from dawam.platform.errors import ApiError

from ..service import Workspace, as_role, authorized, workspace_view
from ..tables import MEMBER_PRIMARY_KEY, OWNER_CONSTRAINT, MemberRecord, WorkspaceRecord
from .policy import INSTALLATION, Action, WorkspaceRole, can


@dataclass(frozen=True)
class Member:
    user_id: uuid.UUID
    email: str
    display_name: str
    role: WorkspaceRole
    is_active: bool
    """False for a user an admin deactivated: still listed, but cannot sign in."""
    added_at: datetime


@dataclass(frozen=True)
class MemberAdded:
    """``add`` found an account with the email and made it a member."""

    member: Member


@dataclass(frozen=True)
class MemberInvited:
    """``add`` found no account with the email and sent an invitation into the
    Workspace instead; accepting it creates the account and the membership."""

    invitation: Invitation
    delivery: Delivery


def _member_not_found() -> ApiError:
    return ApiError(404, "member_not_found", "That user is not a member of this Workspace.")


def _last_owner() -> ApiError:
    return ApiError(
        409,
        "last_owner",
        "A Workspace needs at least one owner. Make another member an owner first.",
    )


def _constraint(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


@contextmanager
def _owner_kept() -> Iterator[None]:
    """Turns the database's owner trigger firing into 409 ``last_owner``. The service
    checks first, so this happens only if that check is ever wrong."""
    try:
        yield
    except IntegrityError as exc:
        if _constraint(exc) == OWNER_CONSTRAINT:
            raise _last_owner() from exc
        raise


class MembershipService:
    """Who belongs to a Workspace and with which role. Every method authorizes through
    the policy (``can``) first."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        clock: Clock,
        auth: AuthService,
        invitations: Invitations,
        registration: RegistrationPolicy | None,
    ) -> None:
        """``registration`` says who may sign up; owners who are not admins may invite
        only the people who could sign up themselves (spec story 31)."""
        self._engine = engine
        self._clock = clock
        self._auth = auth
        self._invitations = invitations
        self._registration = registration

    def list(self, user: User, workspace_id: uuid.UUID) -> list[Member]:
        """The Workspace's members, by display name; for any member."""
        with Session(self._engine) as db:
            authorized(db, user, Action.VIEW_WORKSPACE, workspace_id)
            records = db.scalars(
                sa.select(MemberRecord).where(MemberRecord.workspace_id == workspace_id)
            ).all()
        users = self._auth.users_by_id(record.user_id for record in records)
        members = [_member(record, users[record.user_id]) for record in records]
        return sorted(members, key=lambda m: (m.display_name.casefold(), m.email))

    def add(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        email: str,
        role: WorkspaceRole,
        ip: str | None = None,
    ) -> MemberAdded | MemberInvited:
        """Add the user with ``email`` as ``role``, or invite ``email`` into the Workspace
        if no account has it, acting as ``user`` from ``ip``.

        Raises ``ApiError`` 409 ``already_member``, 403 ``invite_not_allowed`` (no account
        has the email, and ``user`` is not an admin while self-registration would not let
        the email sign up), and the invitation's own errors (422 ``invalid_email``)."""
        as_role(role)
        # Authorized before looking the email up, so only owners learn whether it has an
        # account; the add itself checks again under the lock.
        with Session(self._engine) as db:
            record, _ = authorized(db, user, Action.MANAGE_MEMBERS, workspace_id)
            workspace_name = record.name
        existing = self._auth.find_user_by_email(email)
        if existing is None:
            return self._invite(user, workspace_id, workspace_name, email, role, ip)
        try:
            with Session(self._engine) as db, db.begin():
                authorized(db, user, Action.MANAGE_MEMBERS, workspace_id, lock=True)
                member = MemberRecord(
                    workspace_id=workspace_id,
                    user_id=existing.id,
                    role=role,
                    added_by=user.id,
                    added_at=self._clock(),
                )
                db.add(member)
                _record(
                    db,
                    workspace_id,
                    user.id,
                    "member.added",
                    existing.id,
                    member.added_at,
                    {"role": role},
                )
                db.flush()
                return MemberAdded(member=_member(member, existing))
        except IntegrityError as exc:
            if _constraint(exc) == MEMBER_PRIMARY_KEY:
                raise ApiError(
                    409, "already_member", "That user is already a member of this Workspace."
                ) from None
            raise

    def change_role(
        self, user: User, workspace_id: uuid.UUID, member_id: uuid.UUID, role: WorkspaceRole
    ) -> Member:
        """Give the member ``member_id`` the role ``role``. Raises ``ApiError`` 404
        ``member_not_found``, 409 ``last_owner`` (it would leave no owner)."""
        as_role(role)
        with _owner_kept(), Session(self._engine) as db, db.begin():
            authorized(db, user, Action.MANAGE_MEMBERS, workspace_id, lock=True)
            member = self._member_record(db, workspace_id, member_id)
            if member.role == "owner" and role != "owner":
                self._ensure_another_owner(db, workspace_id)
            if member.role != role:
                _record(
                    db,
                    workspace_id,
                    user.id,
                    "member.role_changed",
                    member_id,
                    self._clock(),
                    {"from": member.role, "to": role},
                )
            member.role = role
            db.flush()
            users = self._auth.users_by_id([member_id])
            return _member(member, users[member_id])

    def remove(self, user: User, workspace_id: uuid.UUID, member_id: uuid.UUID) -> None:
        """Remove the member ``member_id``; their access ends with this call. Raises
        ``ApiError`` 404 ``member_not_found``, 409 ``last_owner``."""
        self._remove_member(user, Action.MANAGE_MEMBERS, workspace_id, member_id)

    def leave(self, user: User, workspace_id: uuid.UUID) -> None:
        """``user`` leaves the Workspace. Raises ``ApiError`` 409 ``last_owner``."""
        self._remove_member(user, Action.LEAVE_WORKSPACE, workspace_id, user.id)

    def transfer_ownership(
        self, user: User, workspace_id: uuid.UUID, to_user_id: uuid.UUID
    ) -> Workspace:
        """Make the member ``to_user_id`` an owner and ``user`` an editor; returns the
        Workspace as ``user`` now sees it. Raises ``ApiError`` 404 ``member_not_found``,
        422 ``invalid_transfer`` (to yourself)."""
        if to_user_id == user.id:
            raise ApiError(
                422, "invalid_transfer", "Choose another member to transfer ownership to."
            )
        with _owner_kept(), Session(self._engine) as db, db.begin():
            record, _ = authorized(db, user, Action.TRANSFER_OWNERSHIP, workspace_id, lock=True)
            target = self._member_record(db, workspace_id, to_user_id)
            target.role = "owner"
            self._member_record(db, workspace_id, user.id).role = "editor"
            _record(
                db,
                workspace_id,
                user.id,
                "workspace.ownership_transferred",
                to_user_id,
                self._clock(),
                {"from_user_id": str(user.id)},
            )
            db.flush()
            return workspace_view(user, record, "editor")

    def _invite(
        self,
        user: User,
        workspace_id: uuid.UUID,
        workspace_name: str,
        email: str,
        role: WorkspaceRole,
        ip: str | None,
    ) -> MemberInvited:
        rules = self._registration.registration_rules() if self._registration else None
        if not can(user, Action.MANAGE_USERS, INSTALLATION) and not (
            rules is not None and rules.allows(email)
        ):
            raise ApiError(
                403,
                "invite_not_allowed",
                "No account has this email, and only an admin can invite new people "
                "while self-registration is closed to them.",
            )
        sent = self._invitations.invite(
            email,
            actor_id=user.id,
            workspace_id=workspace_id,
            workspace_role=role,
            workspace_name=workspace_name,
            ip=ip,
        )
        return MemberInvited(invitation=sent.invitation, delivery=sent.delivery)

    def _remove_member(
        self, user: User, action: Action, workspace_id: uuid.UUID, member_id: uuid.UUID
    ) -> None:
        with _owner_kept(), Session(self._engine) as db, db.begin():
            authorized(db, user, action, workspace_id, lock=True)
            member = self._member_record(db, workspace_id, member_id)
            if member.role == "owner":
                self._ensure_another_owner(db, workspace_id)
            db.delete(member)
            _record(
                db,
                workspace_id,
                user.id,
                "member.left" if member_id == user.id else "member.removed",
                member_id,
                self._clock(),
                {"role": member.role},
            )

    @staticmethod
    def _member_record(db: Session, workspace_id: uuid.UUID, user_id: uuid.UUID) -> MemberRecord:
        member = db.get(MemberRecord, (workspace_id, user_id))
        if member is None:
            raise _member_not_found()
        return member

    @staticmethod
    def _ensure_another_owner(db: Session, workspace_id: uuid.UUID) -> None:
        """Refuse to take away an owner if they are the last one (the Workspace row is
        locked, so the count holds until commit)."""
        owners = db.scalar(
            sa.select(sa.func.count()).where(
                MemberRecord.workspace_id == workspace_id, MemberRecord.role == "owner"
            )
        )
        if owners is not None and owners <= 1:
            raise _last_owner()


class InvitedWorkspaceMembership:
    """The auth module's ``InvitedMembership`` port: accepting an invitation into a
    Workspace makes the new user a member, in the accepting transaction."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    def join(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        role: InvitedRole,
        invited_by: uuid.UUID,
    ) -> bool:
        # Members change only with the Workspace row locked; the invitation's foreign
        # key (ON DELETE CASCADE) means the Workspace still exists.
        status = db.scalar(
            sa.select(WorkspaceRecord.status)
            .where(WorkspaceRecord.id == workspace_id)
            .with_for_update()
        )
        if status == "archived":
            return False  # read-only: nobody joins until it is unarchived
        # Only owners invite into a Workspace, so one removed or demoted since cannot.
        sender = db.get(MemberRecord, (workspace_id, invited_by))
        if sender is None or sender.role != "owner":
            return False
        db.add(
            MemberRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                role=as_role(role),
                added_by=invited_by,
                added_at=self._clock(),
            )
        )
        _record(
            db,
            workspace_id,
            invited_by,
            "member.added",
            user_id,
            self._clock(),
            {"role": role, "invited": True},
        )
        db.flush()
        return True


def _record(
    db: Session,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    verb: str,
    user_id: uuid.UUID,
    at: datetime,
    details: dict[str, object],
) -> None:
    """An activity event about the user ``user_id`` (the feed shows their current name)."""
    record_activity(
        db,
        workspace_id=workspace_id,
        actor_id=actor_id,
        verb=verb,
        object_type="user",
        object_id=user_id,
        details=details,
        at=at,
    )


def _member(record: MemberRecord, user: User) -> Member:
    return Member(
        user_id=record.user_id,
        email=user.email,
        display_name=user.display_name,
        role=as_role(record.role),
        is_active=user.is_active,
        added_at=record.added_at,
    )
