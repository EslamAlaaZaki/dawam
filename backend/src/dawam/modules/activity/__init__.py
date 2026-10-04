"""Activity module: the Workspace activity feed (spec story 38).

Public interface. Other modules import only what is re-exported here:

- ``record_activity(db, *, workspace_id, actor_id, verb, object_type, object_id=None,
  object_label="", details=None, at)``: add an event to a Workspace's feed, in the
  caller's transaction. This is the only way to write activity; no module touches the
  table. Verbs are ``<thing>.<past-tense action>`` (``workspace.updated``).
- ``ActivityService``: reads the feed, newest first, cursor-paginated
  (``ActivityPage`` of ``ActivityItem``, each with its ``ActivityActor``). It does not
  authorize; the caller checks the user may view the Workspace (the workspaces module
  serves ``GET /workspaces/{workspace_id}/activity``).

This module imports only ``auth``, so ``workspaces`` and every later module may import
it (README rule 5). Owns the ``activity_events`` table.
"""

from .service import ActivityActor, ActivityItem, ActivityPage, ActivityService, record_activity

__all__ = [
    "ActivityActor",
    "ActivityItem",
    "ActivityPage",
    "ActivityService",
    "record_activity",
]
