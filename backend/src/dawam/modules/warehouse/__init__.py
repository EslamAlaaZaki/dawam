"""Warehouse module: the Data Warehouse's setup (spec §6.9, story 87).

Public interface. Other modules import only what is re-exported here:

- ``DataWarehouseService``: ``get`` (``None`` before setup), ``set_up`` and ``update``
  for a user, and ``settings_of(workspace_id)`` for a module that already authorized
  the caller and needs the platform, Layer schema names and naming rules.
- ``DataWarehouse``, ``LayerSchemas``, ``NamingRules``, ``DateDimension``: its settings.
- ``platform_profile``, ``max_identifier_length``, ``is_reserved_word`` and
  ``PLATFORM_PROFILES``: each target platform's identifier limit and reserved words,
  for generating valid names.
- ``router``: ``GET|POST|PATCH /workspaces/{workspace_id}/data-warehouse`` and
  ``GET /data-warehouse/platforms``.

Owns the ``data_warehouses`` table.
"""

from .api import router
from .platforms import (
    PLATFORM_PROFILES,
    TARGET_PLATFORMS,
    PlatformProfile,
    TargetPlatform,
    is_reserved_word,
    max_identifier_length,
    platform_profile,
)
from .service import DataWarehouse, DataWarehouseService, DateDimension, LayerSchemas, NamingRules

__all__ = [
    "PLATFORM_PROFILES",
    "TARGET_PLATFORMS",
    "DataWarehouse",
    "DataWarehouseService",
    "DateDimension",
    "LayerSchemas",
    "NamingRules",
    "PlatformProfile",
    "TargetPlatform",
    "is_reserved_word",
    "max_identifier_length",
    "platform_profile",
    "router",
]
