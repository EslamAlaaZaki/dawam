"""Auth module: users, passwords (argon2id), server-side sessions, sign-in and sign-out.

Public interface. Other modules import only what is re-exported here:

- ``AuthService``: create users, the bootstrap admin, sessions.
- ``CurrentUser``: annotate a route parameter with it to require a signed-in user
  (anonymous requests get ``401 unauthenticated``); ``User`` is what it holds.
- ``SecurityEventRecorder``: the one way to record a ``SecurityEvent`` (sign-ins and
  lockouts here; role changes, deactivations, ownership reassignments elsewhere).
- ``router``: ``POST /auth/login``, ``POST /auth/logout``, ``GET /me``.

Owns the ``users``, ``sessions`` and ``security_events`` tables.
"""

from .api import CurrentUser, router
from .internal.security_events import SecurityEvent, SecurityEventRecorder
from .service import AuthService, SessionInfo, SignedIn, SystemRole, User

__all__ = [
    "AuthService",
    "CurrentUser",
    "SecurityEvent",
    "SecurityEventRecorder",
    "SessionInfo",
    "SignedIn",
    "SystemRole",
    "User",
    "router",
]
