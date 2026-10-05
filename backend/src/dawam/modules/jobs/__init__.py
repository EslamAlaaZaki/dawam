"""Jobs module: long work (extraction, profiling, scans, AI jobs, exports) in the background.

Public interface. Other modules import only what is re-exported here:

- ``JobService``: ``submit(workspace_id, job_type, params, title=, created_by=, db=)``
  queues a job, in the caller's transaction when given ``db`` (the caller authorizes
  first); ``get`` / ``list`` / ``cancel`` act for a user through the workspaces policy.
  The worker uses ``claim_next``, ``execute``, ``heartbeat`` and ``fail_lost``.
  ``Job`` and ``JobPage`` are what it returns.
- ``JobRunner`` (the handlers by job type): a module registers ``handler(params, ctx)``
  for its type in ``dawam.job_handlers``; ``ctx`` is a ``JobContext`` (``progress``,
  ``log``, ``raise_if_cancelled``). ``QueuedJobRunner`` leaves jobs for the worker
  process; ``InlineJobRunner`` runs them as soon as they are committed (tests).
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
    UnknownJobTypeError,
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
    "UnknownJobTypeError",
    "router",
]
