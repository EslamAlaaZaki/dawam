"""Admin module: installation-wide settings that admins manage.

Public interface. Other modules import only what is re-exported here:

- ``SystemSettingsService``: read and change the system settings; for now the
  self-registration settings (``RegistrationSettings``). It also implements the auth
  module's ``RegistrationPolicy``, which the composition root wires into sign-up.
- ``router``: ``GET|PUT /admin/settings``, which the central policy (``can`` with
  ``Action.MANAGE_SYSTEM_SETTINGS``) restricts to admins.

Owns the ``system_settings`` table.
"""

from .api import router
from .service import RegistrationSettings, SystemSettingsService

__all__ = ["RegistrationSettings", "SystemSettingsService", "router"]
