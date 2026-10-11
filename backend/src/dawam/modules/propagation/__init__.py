"""Propagation module: change propagation through lineage (spec §6.10, stories 53, 113).

Public interface. Other modules import only what is re-exported here:

- ``PropagationService``: ``propagate`` takes structural changes that were already applied
  (a rename, type change, nullability change or removal of a DW table or column; a Mart
  column newly mapped from Core), plans what they call for in every Layer above and in the
  KPIs (the warehouse's ``plan_propagation`` and the kpis module's
  ``plan_kpi_propagation``), proposes it as **one** ``propagation`` Change Set grouped by
  Layer, and raises an ``impact_alert`` naming the impacted Assets for the owners and
  editors (``PropagationResult``). ``after_edit`` is the warehouse's
  ``StructuralChangeHook`` (a direct edit) and ``after_change_set`` the changesets
  ``ChangeSetAppliedHook`` (an applied AI, regeneration or sync Change Set; a new Snapshot
  is a sync Change Set first, then this one). Applied propagation never propagates again.
- ``structural_changes``: what an applied Change Set changed structurally.

Owns no tables. Imports ``auth``, ``changesets``, ``kpis``, ``notifications``, ``warehouse``
and ``workspaces``.
"""

from .service import PropagationResult, PropagationService, structural_changes

__all__ = ["PropagationResult", "PropagationService", "structural_changes"]
