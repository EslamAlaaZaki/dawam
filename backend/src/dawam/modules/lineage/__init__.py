"""Lineage module: the column-level lineage graph and impact report (spec §6.14, stories 79,
110, 111, 112).

Public interface. Other modules import only what is re-exported here:

- ``LineageService``: ``graph`` walks the stored lineage edges from any node (source or DW
  column, DW table, KPI) upstream, downstream or both, to a depth, and names every node
  (``Graph``, ``Node``, ``Edge``); ``impact`` lists the DW columns, DW tables and KPIs
  downstream of a node (``Impact``). Every call authorizes through the workspaces policy.
- ``LineageService.pii_view``: the PII-derived DW columns and, separately, the PII-influenced
  DW tables (``PiiView``, spec §6.12) from the confirmed findings and the lineage edges;
  each column says whether it is pseudonymised and carries its handling note (``PiiColumn``).
- ``PiiInventoryService``: the PII inventory export (story 140) as XLSX or Markdown: the
  confirmed PII source columns and the PII-derived DW columns with category, hashing,
  handling note and reviewer, and the PII-influenced tables listed separately
  (``PiiInventory``). The ``files`` module saves it to the Data Warehouse's file area.
- ``router``: ``GET /workspaces/{workspace_id}/data-warehouse/lineage?node=&direction=&depth=``
  and ``GET /workspaces/{workspace_id}/data-warehouse/impact?node=`` and
  ``GET /workspaces/{workspace_id}/data-warehouse/pii``, and
  ``GET /workspaces/{workspace_id}/pii/inventory/export?format=xlsx|md``.

Owns no tables: the ``warehouse`` module stores the edges.
"""

from fastapi import APIRouter

from .api import router as _lineage_router
from .pii_inventory import PiiInventory, PiiInventoryService
from .pii_inventory_api import InventoryFormatParam, PiiInventoryServiceDep
from .pii_inventory_api import router as _inventory_router
from .service import Edge, Graph, Impact, LineageService, Node, PiiColumn, PiiView

router = APIRouter()
router.include_router(_lineage_router)
router.include_router(_inventory_router)

__all__ = [
    "Edge",
    "Graph",
    "Impact",
    "InventoryFormatParam",
    "LineageService",
    "Node",
    "PiiColumn",
    "PiiInventory",
    "PiiInventoryService",
    "PiiInventoryServiceDep",
    "PiiView",
    "router",
]
