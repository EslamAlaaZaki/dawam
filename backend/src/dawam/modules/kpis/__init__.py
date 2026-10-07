"""KPIs module: the KPI catalog (spec §6.13, stories 73, 74, 80).

Public interface. Other modules import only what is re-exported here:

- ``KpiService``: document, list, open, edit and delete a Workspace's KPIs (``Kpi``,
  ``KpiPage``, ``KpiTarget``), under a Source System or under the Data Warehouse
  (``source_system_id`` ``None``; no Data Warehouse setup needed). Every call
  authorizes through the workspaces module's policy; every change is audited and
  recorded as activity. ``suggest`` creates the AI's suggestions as draft KPIs
  (``origin`` ``ai``, with a ``rationale``), create-only: it skips names that exist,
  redacts values the PII validators match, and audits each KPI as ``via=ai``.
- ``router``: ``GET|POST /workspaces/{workspace_id}/kpis``,
  ``GET|PATCH|DELETE /workspaces/{workspace_id}/kpis/{kpi_id}``.

Owns the ``kpis`` table.
"""

from .api import router
from .service import Kpi, KpiPage, KpiService, KpiSuggestion, KpiTarget, SuggestedKpis

__all__ = [
    "Kpi",
    "KpiPage",
    "KpiService",
    "KpiSuggestion",
    "KpiTarget",
    "SuggestedKpis",
    "router",
]
