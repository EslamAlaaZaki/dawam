"""Jobs module: long work (extraction, profiling, scans, AI jobs, exports) in the background.

Public interface. Other modules import only what is re-exported here:

- ``JobService``: ``submit(workspace_id, kind, payload, title=, created_by=)`` queues a
  job (the caller authorizes first); ``get`` / ``list`` / ``cancel`` act for a user
  through the workspaces policy. The worker uses ``claim_next``, ``execute``,
  ``heartbeat`` and ``fail_lost``. ``Job`` and ``JobPage`` are what it returns.
- ``JobRunner`` (the handlers by kind): a module registers ``handler(payload, ctx)``
  for its kind at the composition root; ``ctx`` is a ``JobContext`` (``progress``,
  ``log``, ``raise_if_cancelled``). ``QueuedJobRunner`` leaves jobs for the worker
  process; ``InlineJobRunner`` runs them inside ``submit`` (tests).
- ``JobService.cancel_for_workspace``: the ``WorkspaceArchivedHook`` the composition
  root hands the workspaces module, so archiving cancels the Workspace's jobs.
- A finished or failed job notifies its creator (``job`` notification); a job whose
  worker died is failed with a reason.
- ``router``: ``GET /jobs/{job_id}``, ``POST /jobs/{job_id}/cancel``,
  ``GET /workspaces/{workspace_id}/jobs``.

Owns the ``jobs`` table. Imports ``auth``, ``notifications`` and ``workspaces``.
"""

from .api import router
from .service import (
    InlineJobRunner,
    Job,
    JobCancelledError,
    JobContext,
    JobHandler,
    JobPage,
    JobRunner,
    JobService,
    QueuedJobRunner,
    UnknownJobKindError,
)

__all__ = [
    "InlineJobRunner",
    "Job",
    "JobCancelledError",
    "JobContext",
    "JobHandler",
    "JobPage",
    "JobRunner",
    "JobService",
    "QueuedJobRunner",
    "UnknownJobKindError",
    "router",
]
