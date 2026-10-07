"""Changesets module: the Change Set engine (spec §6.10 "Change Set rules", stories 147, 148).

Public interface. Other modules import only what is re-exported here:

- ``ChangeSetService``: ``propose`` a Change Set (items with their base values,
  dependencies and the role each needs, derived from the permission matrix; a pending
  Change Set of the same origin and scope is superseded), ``get`` / ``list`` it as a diff,
  and ``accept`` / ``reject`` all or some items with dependency closure. Accepting is one
  transaction on the locked Change Set: stale items (a field they change has changed
  since) and their dependents are skipped and reported, owner-only items stay
  ``needs_owner`` (and notify the owners) unless an owner accepts, everything else is
  applied and audited. Closing a Change Set expires its remaining items. Every call
  authorizes through the workspaces policy.
- ``ObjectHandler`` / ``ObjectHandlers`` / ``AppliedChange``: a module that owns objects a
  Change Set may change registers one handler per object type (the composition root
  builds ``ObjectHandlers`` and sets ``app.state.change_set_handlers``); the engine
  never imports those modules.
- ``reject_pending_change_sets``: the ``WorkspaceArchivedHook`` that rejects a Workspace's
  pending Change Sets when it is archived.
- ``router``: ``GET /workspaces/{workspace_id}/change-sets``, ``GET .../{change_set_id}``,
  ``POST .../{change_set_id}/accept`` and ``POST .../{change_set_id}/reject``.

Owns the ``change_sets`` and ``change_set_items`` tables. Imports ``auth``, ``audit``,
``activity``, ``notifications`` and ``workspaces``.
"""

from .api import router
from .handlers import AppliedChange, ObjectHandler, ObjectHandlers
from .service import (
    MAX_ITEMS,
    ApplyResult,
    ChangeSet,
    ChangeSetDetail,
    ChangeSetItem,
    ChangeSetPage,
    ChangeSetService,
    ProposedItem,
    SkippedItem,
    reject_pending_change_sets,
)

__all__ = [
    "MAX_ITEMS",
    "AppliedChange",
    "ApplyResult",
    "ChangeSet",
    "ChangeSetDetail",
    "ChangeSetItem",
    "ChangeSetPage",
    "ChangeSetService",
    "ObjectHandler",
    "ObjectHandlers",
    "ProposedItem",
    "SkippedItem",
    "reject_pending_change_sets",
    "router",
]
