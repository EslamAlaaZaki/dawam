"""Audit module: the audit trail of critical entities (spec §6.10, story 127).

Public interface. Other modules import only what is re-exported here:

- ``record_audit(db, *, workspace_id, actor_id, entity_type, entity_id, old, new, at,
  via="user")``: add an entry (who, when, how, old and new values) in the caller's
  transaction, so an entry exists exactly when its change does. This is the only way
  to write the trail. Entries never hold secrets: callers pass only the fields that
  changed (or the whole entity at creation or deletion), never credentials.
- ``AuditService.list``: the entries of one entity, oldest first. It does not
  authorize; the caller checks the user may view the Workspace.

This module imports nothing from other modules, so any module may import it (README
rule 5). Owns the ``audit_entries`` table.
"""

from .service import VIA_VALUES, AuditEntry, AuditService, record_audit

__all__ = ["VIA_VALUES", "AuditEntry", "AuditService", "record_audit"]
