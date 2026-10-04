"""Auth module: users, passwords (argon2id), server-side sessions, sign-in and sign-out.

Public interface. Other modules import only what is re-exported here:

- ``AuthService``: create users, the bootstrap admin, self-registration, sessions,
  display names and password changes.
- ``RegistrationPolicy``: the port sign-up reads its ``RegistrationRules`` from
  (whether registration is open, and to which domains); the composition root sets
  ``app.state.registration_policy``.
- ``CurrentUser``: annotate a route parameter with it to require a signed-in user
  (anonymous requests get ``401 unauthenticated``); ``User`` is what it holds.
- ``AdminUser``: the same, for admin-only routes (others get ``403 forbidden``).
- ``SecurityEventRecorder``: the one way to record a ``SecurityEvent`` (sign-ins and
  lockouts here; role changes, deactivations, ownership reassignments elsewhere).
- ``PasswordResets``: forgotten passwords (reset links, emailed through the
  ``dawam.platform.email.Mailer`` the composition root provides as
  ``app.state.mailer``: auth never imports another module).
- ``router``: ``GET /auth/registration``; ``POST /auth/register``, ``/auth/login``,
  ``/auth/logout``, ``/auth/logout-all``, ``/auth/password/change``,
  ``/auth/password/forgot``, ``/auth/password/reset``; ``GET|PATCH /me``.

Owns the ``users``, ``sessions``, ``security_events`` and ``password_resets`` tables.
"""

from .api import AdminUser, CurrentUser, router
from .internal.security_events import SecurityEvent, SecurityEventRecorder
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
    "AdminUser",
    "AuthService",
    "CurrentUser",
    "PasswordResets",
    "RegistrationPolicy",
    "RegistrationRules",
    "SecurityEvent",
    "SecurityEventRecorder",
    "SessionInfo",
    "SignedIn",
    "SystemRole",
    "User",
    "router",
]
