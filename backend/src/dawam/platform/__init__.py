"""Shared kernel: configuration, database, errors, logging, migrations, email.

It also holds the app-wide HTTP plumbing that belongs to no module: the request
context middleware (``request_context``), the ``/healthz`` and ``/readyz`` probes
(``health``) and ``GET /api/v1/version`` (``meta``).

Every module may import from here. Nothing here may import ``dawam.modules`` or
``dawam.app`` (enforced by ``tools/check_boundaries.py``).
"""
