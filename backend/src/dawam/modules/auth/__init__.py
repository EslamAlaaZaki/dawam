"""Auth module: users, passwords (argon2id), server-side sessions, sign-in and sign-out.

Public interface. Other modules import only what is re-exported here:

- ``AuthService``: create users, the bootstrap admin, self-registration, sessions,
  display names and password changes.
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
- ``router``: ``GET /auth/registration``; ``POST /auth/register``, ``/auth/login``,
  ``/auth/logout``, ``/auth/logout-all``, ``/auth/password/change``,
  ``/auth/password/forgot``, ``/auth/password/reset``; ``GET|PATCH /me``.

Owns the ``users``, ``sessions``, ``security_events`` and ``password_resets`` tables.
"""

from .api import CurrentUser, router
from .internal.credentials import MAX_EMAIL_LENGTH, MAX_PASSWORD_LENGTH
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
    "LoggedSecurityEvent",
    "PasswordResets",
    "RegistrationPolicy",
    "RegistrationRules",
    "SecurityEvent",
    "SecurityEventPage",
    "SecurityEventRecorder",
    "SessionInfo",
    "SignedIn",
    "SystemRole",
    "User",
    "UserAdministration",
    "UserPage",
    "router",
]
