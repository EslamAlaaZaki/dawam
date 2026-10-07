"""Collaboration module: comments and @mentions (spec §4.3, stories 107, 125, 126).

Public interface. Other modules import only what is re-exported here:

- ``CommentService``: ``add`` a comment on a table, column, DW object, KPI or mapping
  (``object_type`` + ``object_id``) or a reply to a thread, ``threads`` of one object
  (``CommentThread`` of ``Comment``), ``resolve`` / ``reopen`` a thread. Viewers comment
  too. Every call authorizes through the workspaces module's policy. A mentioned Workspace
  member gets a ``mention`` notification (through the notifications module).
- ``router``: ``GET|POST /workspaces/{workspace_id}/comments`` (``GET`` takes
  ``object_type`` and ``object_id``), ``POST .../comments/{comment_id}/resolve`` and
  ``.../reopen``.

Owns the ``comments`` table. The object ids are opaque: a comment is keyed by Workspace,
type and id, so it cannot expose another Workspace's data.
"""

from .api import router
from .service import Comment, CommentService, CommentThread, Person

__all__ = ["Comment", "CommentService", "CommentThread", "Person", "router"]
