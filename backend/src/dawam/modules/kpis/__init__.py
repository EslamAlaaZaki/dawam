"""KPIs module: the KPI catalog (spec §6.13, stories 73, 74, 80).

Public interface. Other modules import only what is re-exported here:

- ``KpiService``: document, list, open, edit and delete a Workspace's KPIs (``Kpi``,
  ``KpiPage``, ``KpiTarget``), under a Source System or under the Data Warehouse
  (``source_system_id`` ``None``; no Data Warehouse setup needed). Every call
  authorizes through the workspaces module's policy; every change is audited and
  recorded as activity. ``suggest`` creates the AI's suggestions as draft KPIs
  (``origin`` ``ai``, with a ``rationale``), create-only: it skips names that exist,
  redacts values the PII validators match, and audits each KPI as ``via=ai``.
  Formula SQL is one SELECT query validated with sqlglot against the Core and Mart tables of
  the Data Warehouse (only parsed before it is set up). ``links`` / ``set_links`` read and
  replace the DW columns a KPI uses (``KpiLinks``, ``KpiLink``), at the highest Layer
  holding the measure, stored with ``kpi`` lineage edges; a structural edit of an approved
  KPI returns it to draft. ``link_candidates`` (``LinkCandidates``) lists the KPIs with no
  links or no formula SQL and the DW Schema, for the AI to propose them.
- ``rule_suggestions`` / ``accept_suggestion`` / ``dismiss_suggestion`` (``RuleSuggestion``):
  the rule-based KPI suggestions (spec §6.13, story 77): a computed list from the DW Schema
  (sum and average of additive measures, distinct count of dimension keys, ratios, month-over-
  month and year-over-year growth; one per Core measure through lineage), each with formula SQL
  and a feasibility. Accepting one creates a draft KPI (``origin`` ``rule``) with its links;
  a dismissed one stays hidden. ``feasibility`` (``KpiFeasibility``) tells for any KPI,
  an AI suggestion included, whether it is *computable*, *needs mapping* or *needs data*.
- ``kpi_names`` (in the caller's session, no permission check): the names of the given
  KPIs of a Workspace, for the lineage graph's ``kpi`` nodes.
- ``kpis_for_score`` (in the caller's session, no permission check): the Workspace's KPIs as
  the warehouse score's DW-wide KPI checks read them (``ScoreService(kpis=...)``); a change
  to a KPI or its links asks for a rescore.
- ``KpiLinkHandler``: the Change Set handler for ``kpi`` objects (``formula_sql`` and
  ``links``); the composition root registers it.
- ``plan_kpi_propagation`` (in the caller's session, no permission check): the KPI items of a
  propagation (``KpiPlan``): formula SQL respelled after a rename, links and formula
  re-pointed from Core to the Mart column now mapped from it, and the KPIs a removal breaks.
  ``KpiPropagationHandler`` is the handler for its ``kpi_formula`` items.
- For the ``deletions`` module (in the caller's session, no permission check):
  ``system_kpis`` / ``release_system_kpis`` (``SystemKpis``, ``KpiRef``) read and apply what a
  Source System's permanent delete does to its KPIs (linked ones re-homed to the Data
  Warehouse, the rest deleted), and ``kpis_linked_to`` names the KPIs linked to DW columns.
- ``plan_kpi_transpile`` (in the caller's session, no permission check): the ``kpi_formula``
  items (and "manual" flags) that move KPI formulas to a new target platform's dialect, for
  the warehouse's platform change; ``kpi_formulas_check`` fills the warehouse's port that
  refuses a direct platform edit while a KPI holds formula SQL.
- ``router``: ``GET|POST /workspaces/{workspace_id}/kpis``,
  ``GET|PATCH|DELETE /workspaces/{workspace_id}/kpis/{kpi_id}``,
  ``GET|PUT /workspaces/{workspace_id}/kpis/{kpi_id}/links``,
  ``GET /workspaces/{workspace_id}/kpis/{kpi_id}/feasibility``,
  ``GET /workspaces/{workspace_id}/kpis/suggestions`` and ``POST .../suggestions/accept|dismiss``.

Owns the ``kpis``, ``kpi_links`` and ``kpi_suggestion_dismissals`` tables.
"""

from .api import router
from .deletion import KpiRef, SystemKpis, kpis_linked_to, release_system_kpis, system_kpis
from .link_changes import KpiLinkHandler
from .links import kpi_names
from .platform_change import kpi_formulas_check, plan_kpi_transpile
from .propagation import KpiPlan, KpiPropagationHandler, plan_kpi_propagation
from .score_feed import kpis_for_score
from .service import (
    Kpi,
    KpiFeasibility,
    KpiLink,
    KpiLinks,
    KpiPage,
    KpiService,
    KpiSuggestion,
    KpiTarget,
    LinkCandidates,
    SuggestedKpis,
)
from .suggestions import RuleSuggestion

__all__ = [
    "Kpi",
    "KpiFeasibility",
    "KpiLink",
    "KpiLinkHandler",
    "KpiLinks",
    "KpiPage",
    "KpiPlan",
    "KpiPropagationHandler",
    "KpiRef",
    "KpiService",
    "KpiSuggestion",
    "KpiTarget",
    "LinkCandidates",
    "RuleSuggestion",
    "SuggestedKpis",
    "SystemKpis",
    "kpi_formulas_check",
    "kpi_names",
    "kpis_for_score",
    "kpis_linked_to",
    "plan_kpi_propagation",
    "plan_kpi_transpile",
    "release_system_kpis",
    "router",
    "system_kpis",
]
