"""Business modules of the modular monolith. See README.md in this directory.

``ALL_MODULES`` lists every module's public interface. The app factory mounts each
module's ``router`` (if it has one), and importing the modules registers their tables
on ``dawam.platform.db.Base.metadata`` for Alembic.
"""

from types import ModuleType

from dawam.modules import admin, auth, jobs, mail, warehouse, workspaces

ALL_MODULES: tuple[ModuleType, ...] = (auth, admin, jobs, mail, warehouse, workspaces)

__all__ = ["ALL_MODULES"]
