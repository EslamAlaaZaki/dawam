"""Auth module: users, passwords (argon2id), server-side sessions, sign-in and sign-out.

Public interface. Other modules import only what is re-exported here:

- ``AuthService``: create users, the bootstrap admin, sessions.
- ``CurrentUser``: annotate a route parameter with it to require a signed-in user
  (anonymous requests get ``401 unauthenticated``); ``User`` is what it holds.
- ``router``: ``POST /auth/login``, ``POST /auth/logout``, ``GET /me``.

Owns the ``users`` and ``sessions`` tables.
"""

from .api import CurrentUser, router
from .service import MIN_PASSWORD_LENGTH, AuthService, SignedIn, SystemRole, User

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "AuthService",
    "CurrentUser",
    "SignedIn",
    "SystemRole",
    "User",
    "router",
]
