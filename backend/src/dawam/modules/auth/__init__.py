"""Auth module: users, passwords (argon2id), server-side sessions, sign-in and sign-out.

Public interface. Other modules import only what is re-exported here:

- ``AuthService``: create users, the bootstrap admin, self-registration, sessions,
  display names and password changes; look users up by id or email.
- ``RegistrationPolicy``: the port sign-up reads its ``RegistrationRules`` from
  (whether registration is open, and to which domains); the composition root sets
  ``app.state.registration_policy``.
- ``CurrentUser``: annotate a route parameter with it to require a signed-in user
  (anonymous requests get ``401 unauthenticated``; a user who must change a temporary
  password first gets ``403 password_change_required``); ``User`` is what it holds.
- ``SecurityEventRecorder``: the one way to record a ``SecurityEvent`` (sign-ins and
  lockouts here; role changes, deactivations, ownership reassignments elsewhere), and
  to ``search`` the log (a ``SecurityEventPage`` of ``LoggedSecurityEvent``).
- ``UserAdministration``: what admins do to accounts: list, search and filter users
  (``UserPage``), create them with a temporary password, change their role and
  deactivate or reactivate them. ``PasswordResets.force_reset`` forces a reset.
- ``MAX_EMAIL_LENGTH``, ``MAX_PASSWORD_LENGTH``: bounds for request bodies.
- ``PasswordResets``: forgotten passwords (reset links, emailed through the
  ``dawam.platform.email.Mailer`` the composition root provides as
  ``app.state.mailer``: auth never imports another module).
- ``Invitations``: inviting people by email (``SentInvitation``, pending ones as an
  ``InvitationPage`` of ``Invitation``, revoking) and accepting an invitation; the
  links go out through the same ``Mailer``. Accepting an invitation into a Workspace
  joins it through the ``InvitedMembership`` port (the ``workspaces`` module implements
  it; the composition root sets ``app.state.invited_membership``).
- ``router``: ``GET /auth/registration``; ``POST /auth/register``, ``/auth/login``,
  ``/auth/logout``, ``/auth/logout-all``, ``/auth/password/change``,
  ``/auth/password/forgot``, ``/auth/password/reset``, ``/auth/invitations/lookup``,
  ``/auth/invitations/accept``; ``GET|PATCH /me``.

Owns the ``users``, ``sessions``, ``security_events``, ``password_resets`` and
``invitations`` tables.
"""

from .api import CurrentUser, router
from .internal.credentials import MAX_EMAIL_LENGTH, MAX_PASSWORD_LENGTH
from .internal.invitations import (
    Invitation,
    InvitationPage,
    Invitations,
    InvitedMembership,
    InvitedRole,
    Inviter,
    SentInvitation,
)
from .internal.security_events import (
    LoggedSecurityEvent,
    SecurityEvent,
    SecurityEventPage,
    SecurityEventRecorder,
)
from .internal.user_admin import UserAdministration, UserPage
from .service import (
    AuthService,
    PasswordResets,
    RegistrationPolicy,
    RegistrationRules,
    SessionInfo,
    SignedIn,
    SystemRole,
    User,
)

__all__ = [
    "MAX_EMAIL_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "AuthService",
    "CurrentUser",
    "Invitation",
    "InvitationPage",
    "Invitations",
    "InvitedMembership",
    "InvitedRole",
    "Inviter",
    "LoggedSecurityEvent",
    "PasswordResets",
    "RegistrationPolicy",
    "RegistrationRules",
    "SecurityEvent",
    "SecurityEventPage",
    "SecurityEventRecorder",
    "SentInvitation",
    "SessionInfo",
    "SignedIn",
    "SystemRole",
    "User",
    "UserAdministration",
    "UserPage",
    "router",
]
