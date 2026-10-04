"""Admin module: installation-wide settings that admins manage.

Public interface. Other modules import only what is re-exported here:

- ``SystemSettingsService``: read and change the system settings; for now the
  self-registration settings (``RegistrationSettings``). It also implements the auth
  module's ``RegistrationPolicy``, which the composition root wires into sign-up.
- ``AdminUser``: annotate a route parameter with it to make the route admin-only
  (``403 forbidden`` for other users); a stand-in for the central policy (#29).
- ``router``: ``GET|PUT /admin/settings``.

Owns the ``system_settings`` table.
"""

from .api import AdminUser, router
from .service import RegistrationSettings, SystemSettingsService

__all__ = ["AdminUser", "RegistrationSettings", "SystemSettingsService", "router"]
