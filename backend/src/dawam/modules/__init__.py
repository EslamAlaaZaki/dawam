"""Business modules of the modular monolith. See README.md in this directory.

``ALL_MODULES`` lists every module's public interface. The app factory mounts each
module's ``router`` (if it has one), and importing the modules registers their tables
on ``dawam.platform.db.Base.metadata`` for Alembic.
"""

from types import ModuleType

from dawam.modules import (
    activity,
    admin,
    audit,
    auth,
    files,
    jobs,
    kpis,
    mail,
    notifications,
    sources,
    warehouse,
    workspaces,
)

ALL_MODULES: tuple[ModuleType, ...] = (
    auth,
    admin,
    activity,
    audit,
    jobs,
    mail,
    warehouse,
    sources,
    notifications,
    workspaces,
    kpis,
    files,
)

__all__ = ["ALL_MODULES"]
