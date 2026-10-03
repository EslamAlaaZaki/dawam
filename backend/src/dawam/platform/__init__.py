"""Shared kernel: configuration, database, errors, logging, migrations, email.

Every module may import from here. Nothing here may import ``dawam.modules`` or
``dawam.app`` (enforced by ``tools/check_boundaries.py``).
"""
