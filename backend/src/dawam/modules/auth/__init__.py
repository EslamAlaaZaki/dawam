"""Auth module: users, passwords (argon2id), server-side sessions, sign-in and sign-out.

Public interface. Other modules import only what is re-exported here:

- ``AuthService``: create users, the bootstrap admin, self-registration, sessions,
  display names and password changes.
- ``RegistrationPolicy``: the port sign-up asks whether registration is open and a
  domain allowed; the composition root sets ``app.state.registration_policy``.
- ``CurrentUser``: annotate a route parameter with it to require a signed-in user
  (anonymous requests get ``401 unauthenticated``); ``User`` is what it holds.
- ``SecurityEventRecorder``: the one way to record a ``SecurityEvent`` (sign-ins and
  lockouts here; role changes, deactivations, ownership reassignments elsewhere).
- ``router``: ``GET /auth/registration``; ``POST /auth/register``, ``/auth/login``,
  ``/auth/logout``, ``/auth/logout-all``, ``/auth/password/change``; ``GET|PATCH /me``.

Owns the ``users``, ``sessions`` and ``security_events`` tables.
"""

from .api import CurrentUser, router
from .internal.security_events import SecurityEvent, SecurityEventRecorder
from .service import AuthService, RegistrationPolicy, SessionInfo, SignedIn, SystemRole, User

__all__ = [
    "AuthService",
    "CurrentUser",
    "RegistrationPolicy",
    "SecurityEvent",
    "SecurityEventRecorder",
    "SessionInfo",
    "SignedIn",
    "SystemRole",
    "User",
    "router",
]
