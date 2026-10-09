"""Lineage module: the column-level lineage graph and impact report (spec §6.14, stories 79,
110, 111, 112).

Public interface. Other modules import only what is re-exported here:

- ``LineageService``: ``graph`` walks the stored lineage edges from any node (source or DW
  column, DW table, KPI) upstream, downstream or both, to a depth, and names every node
  (``Graph``, ``Node``, ``Edge``); ``impact`` lists the DW columns, DW tables and KPIs
  downstream of a node (``Impact``). Every call authorizes through the workspaces policy.
- ``LineageService.pii_view``: the PII-derived DW columns and, separately, the PII-influenced
  DW tables (``PiiView``, spec §6.12) from the confirmed findings and the lineage edges.
- ``router``: ``GET /workspaces/{workspace_id}/data-warehouse/lineage?node=&direction=&depth=``
  and ``GET /workspaces/{workspace_id}/data-warehouse/impact?node=`` and
  ``GET /workspaces/{workspace_id}/data-warehouse/pii``.

Owns no tables: the ``warehouse`` module stores the edges.
"""

from .api import router
from .service import Edge, Graph, Impact, LineageService, Node, PiiView

__all__ = ["Edge", "Graph", "Impact", "LineageService", "Node", "PiiView", "router"]
