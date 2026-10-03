"""Jobs module: running long work in the background.

Public interface. Other modules import only what is re-exported here.

For now there is only the inline runner: ``submit`` runs the handler before it
returns. #41 adds the Postgres-backed queue (``SELECT ... FOR UPDATE SKIP LOCKED``)
consumed by the worker process; tests keep using ``InlineJobRunner`` so background
work stays synchronous and deterministic in the S1 harness.
"""

from .runner import InlineJobRunner, JobHandler, JobRunner, UnknownJobKindError

__all__ = ["InlineJobRunner", "JobHandler", "JobRunner", "UnknownJobKindError"]
