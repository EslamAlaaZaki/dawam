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
- ``KpiLinkHandler``: the Change Set handler for ``kpi`` objects (``formula_sql`` and
  ``links``); the composition root registers it.
- ``router``: ``GET|POST /workspaces/{workspace_id}/kpis``,
  ``GET|PATCH|DELETE /workspaces/{workspace_id}/kpis/{kpi_id}``,
  ``GET|PUT /workspaces/{workspace_id}/kpis/{kpi_id}/links``.

Owns the ``kpis`` and ``kpi_links`` tables.
"""

from .api import router
from .link_changes import KpiLinkHandler
from .service import (
    Kpi,
    KpiLink,
    KpiLinks,
    KpiPage,
    KpiService,
    KpiSuggestion,
    KpiTarget,
    LinkCandidates,
    SuggestedKpis,
)

__all__ = [
    "Kpi",
    "KpiLink",
    "KpiLinkHandler",
    "KpiLinks",
    "KpiPage",
    "KpiService",
    "KpiSuggestion",
    "KpiTarget",
    "LinkCandidates",
    "SuggestedKpis",
    "router",
]
