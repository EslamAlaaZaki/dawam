"""The documentation pack (spec §6.15, story 128): the Workspace's Source Schemas, DW Schema
per Layer, KPIs, mappings and a lineage summary, as zipped Markdown pages or one HTML file."""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any

from dawam.modules.auth import User
from dawam.modules.kpis import Kpi, KpiLink, KpiService
from dawam.modules.sources import SnapshotService, SourceSystem, SourceSystemService
from dawam.modules.warehouse import DataWarehouseService, MappingService, ModelService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.errors import ApiError

from .render import Block, Bullets, Heading, Page, Paragraph, Table, markdown, single_html

ZIP_MIME = "application/zip"
HTML_MIME = "text/html"
MARKDOWN_MIME = "text/markdown"
LAYERS = ("staging", "core", "mart")
MAPPING_LAYERS = ("core", "mart")
KPI_PAGE_SIZE = 100


@dataclass(frozen=True)
class DocumentationPack:
    name: str
    mime: str
    data: bytes


def _data_type(spec: dict[str, Any]) -> str:
    """A neutral DW type as text, e.g. ``string(64)`` or ``decimal(18,2)``."""
    name = str(spec.get("type", ""))
    if spec.get("precision") is not None:
        scale = spec.get("scale")
        return f"{name}({spec['precision']}{'' if scale is None else f',{scale}'})"
    if spec.get("length") is not None:
        return f"{name}({spec['length']})"
    return name


def _yes(value: bool | None) -> str:
    return "" if value is None else ("yes" if value else "no")


class DocumentationPackService:
    """Authorizes through the services it wraps (any member reads)."""

    def __init__(
        self,
        *,
        workspaces: WorkspaceService,
        systems: SourceSystemService,
        snapshots: SnapshotService,
        warehouses: DataWarehouseService,
        model: ModelService,
        mappings: MappingService,
        kpis: KpiService,
    ) -> None:
        self._workspaces = workspaces
        self._systems = systems
        self._snapshots = snapshots
        self._warehouses = warehouses
        self._model = model
        self._mappings = mappings
        self._kpis = kpis

    # --- exports ---------------------------------------------------------------------

    def zip(self, user: User, workspace_id: uuid.UUID) -> DocumentationPack:
        """The pack as Markdown files in a zip: ``README.md`` and a page per Source System,
        Layer and topic."""
        _, pages = self._pages(user, workspace_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for page in pages:
                archive.writestr(page.path, markdown(page.blocks))
        return DocumentationPack("documentation-pack.zip", ZIP_MIME, buffer.getvalue())

    def html(self, user: User, workspace_id: uuid.UUID) -> DocumentationPack:
        """The pack as one self-contained HTML file."""
        title, pages = self._pages(user, workspace_id)
        return DocumentationPack(
            "documentation-pack.html", HTML_MIME, single_html(title, pages).encode()
        )

    def combined_markdown(self, user: User, workspace_id: uuid.UUID) -> DocumentationPack:
        """The pack as one Markdown file, for the file area (which stores no HTML or zip)."""
        _, pages = self._pages(user, workspace_id)
        parts = [markdown(page.blocks) for page in pages]
        return DocumentationPack("documentation-pack.md", MARKDOWN_MIME, "\n".join(parts).encode())

    # --- pages -----------------------------------------------------------------------

    def _pages(self, user: User, workspace_id: uuid.UUID) -> tuple[str, list[Page]]:
        workspace = self._workspaces.get(user, workspace_id)
        systems = self._all_systems(user, workspace_id)
        warehouse = self._warehouses.get(user, workspace_id)
        kpis = self._all_kpis(user, workspace_id)
        pages = [self._index(workspace.name, workspace.description, systems, warehouse is not None)]
        pages += [self._source_page(user, workspace_id, s) for s in systems]
        tables: dict[str, list[Any]] = {layer: [] for layer in LAYERS}
        if warehouse is not None:
            for layer in LAYERS:
                tables[layer] = [
                    self._model.get_table(user, workspace_id, s.id)
                    for s in self._model.list_tables(user, workspace_id, layer=layer)
                ]
            platform = str(warehouse.target_platform)
        else:
            platform = ""
        pages += [self._layer_page(layer, platform, tables[layer]) for layer in LAYERS]
        views = {
            layer: [self._mappings.get_mapping(user, workspace_id, t.id) for t in tables[layer]]
            for layer in MAPPING_LAYERS
        }
        pages += [self._mapping_page(layer, views[layer]) for layer in MAPPING_LAYERS]
        links = {k.id: self._kpis.links(user, workspace_id, k.id).items for k in kpis}
        pages.append(self._kpi_page(systems, kpis, links))
        pages.append(self._lineage_page(views, kpis, links))
        return f"Documentation pack: {workspace.name}", pages

    def _all_systems(self, user: User, workspace_id: uuid.UUID) -> list[SourceSystem]:
        systems: list[SourceSystem] = []
        cursor = None
        while True:
            page = self._systems.list(user, workspace_id, limit=100, cursor=cursor)
            systems += page.items
            cursor = page.next_cursor
            if cursor is None:
                return systems

    def _all_kpis(self, user: User, workspace_id: uuid.UUID) -> list[Kpi]:
        kpis: list[Kpi] = []
        cursor = None
        while True:
            page = self._kpis.list(user, workspace_id, limit=KPI_PAGE_SIZE, cursor=cursor)
            kpis += page.items
            cursor = page.next_cursor
            if cursor is None:
                return kpis

    @staticmethod
    def _index(
        name: str, description: str, systems: list[SourceSystem], has_warehouse: bool
    ) -> Page:
        blocks: list[Block] = [Heading(1, f"Documentation pack: {name}")]
        if description:
            blocks.append(Paragraph(description))
        blocks.append(Heading(2, "Contents"))
        items = [
            f"Source Schema: {s.name} (`{s.code}`), source-schemas/{s.code}.md" for s in systems
        ]
        items += [f"DW Schema, {layer} Layer: dw-schema/{layer}.md" for layer in LAYERS]
        items += [f"Mappings, {layer} Layer: mappings/{layer}.md" for layer in MAPPING_LAYERS]
        items += ["KPIs: kpis.md", "Lineage summary: lineage.md"]
        blocks.append(Bullets(items))
        if not has_warehouse:
            blocks.append(Paragraph("The Data Warehouse is not set up yet."))
        return Page("README.md", "Overview", blocks)

    def _source_page(self, user: User, workspace_id: uuid.UUID, system: SourceSystem) -> Page:
        blocks: list[Block] = [Heading(1, f"Source Schema: {system.name} ({system.code})")]
        if system.description:
            blocks.append(Paragraph(system.description))
        owners = [
            f"{label}: {value}"
            for label, value in (
                ("Business owner", system.business_owner),
                ("Technical owner", system.technical_owner),
            )
            if value
        ]
        if owners:
            blocks.append(Bullets(owners))
        try:
            schema = self._snapshots.source_schema(user, workspace_id, system.id)
        except ApiError as exc:
            if exc.status_code != 404:
                raise
            blocks.append(Paragraph("No Snapshot has been taken yet."))
            return Page(f"source-schemas/{system.code}.md", f"Source Schema: {system.name}", blocks)
        tables = sorted(schema.content.tables, key=lambda t: (t.db_schema, t.name))
        for table in tables:
            blocks.append(Heading(2, f"{table.db_schema}.{table.name}"))
            facts = [f"Kind: {table.kind}"]
            if table.classification:
                facts.append(f"Classification: {table.classification}")
            if table.tags:
                facts.append(f"Tags: {', '.join(table.tags)}")
            blocks.append(Bullets(facts))
            if table.description:
                blocks.append(Paragraph(table.description))
            blocks.append(
                Table(
                    ["Column", "Type", "Nullable", "Primary key", "PII category", "Description"],
                    [
                        [
                            c.name,
                            c.data_type,
                            _yes(c.is_nullable),
                            _yes(c.is_pk),
                            c.pii_category or "",
                            c.description or c.comment or "",
                        ]
                        for c in sorted(table.columns, key=lambda c: c.ordinal)
                    ],
                )
            )
        if not tables:
            blocks.append(Paragraph("The latest Snapshot has no tables."))
        return Page(f"source-schemas/{system.code}.md", f"Source Schema: {system.name}", blocks)

    @staticmethod
    def _layer_page(layer: str, platform: str, tables: list[Any]) -> Page:
        blocks: list[Block] = [Heading(1, f"DW Schema: {layer} Layer")]
        if platform:
            blocks.append(Paragraph(f"Target platform: {platform}."))
        if not tables:
            blocks.append(Paragraph("No tables in this Layer."))
        for t in sorted(tables, key=lambda t: t.name.lower()):
            blocks.append(Heading(2, t.name))
            facts = [f"Kind: {t.kind}"]
            if t.fact_type:
                facts.append(f"Fact type: {t.fact_type}")
            if t.grain:
                facts.append(f"Grain: {t.grain}")
            if t.scd_type is not None:
                facts.append(f"SCD type: {t.scd_type}")
            blocks.append(Bullets(facts))
            if t.description:
                blocks.append(Paragraph(t.description))
            blocks.append(
                Table(
                    ["Column", "Type", "Nullable", "Role", "Description", "Note"],
                    [
                        [
                            c.name,
                            _data_type(c.data_type),
                            _yes(c.is_nullable),
                            c.role,
                            c.description,
                            c.pii_note or "",
                        ]
                        for c in sorted(t.columns, key=lambda c: c.ordinal)
                    ],
                )
            )
        return Page(f"dw-schema/{layer}.md", f"DW Schema: {layer} Layer", blocks)

    @staticmethod
    def _mapping_page(layer: str, views: list[Any]) -> Page:
        blocks: list[Block] = [Heading(1, f"Mappings: {layer} Layer")]
        if not views:
            blocks.append(Paragraph("No tables in this Layer."))
        header = ["Column", "Type", "Inputs", "Rule", "SQL"]
        for view in sorted(views, key=lambda v: v.table_name.lower()):
            blocks.append(Heading(2, view.table_name))
            blocks.append(Paragraph(f"Built from the {view.source_layer} Layer."))
            if view.integration_rule:
                blocks.append(Paragraph(f"Integration rule: {view.integration_rule}"))
            if view.match_keys:
                blocks.append(Paragraph(f"Match keys: {', '.join(view.match_keys)}"))
            if view.notes:
                blocks.append(Paragraph(view.notes))

            def rows(columns: list[Any]) -> list[list[str]]:
                return [
                    [
                        c.column_name,
                        c.mapping_type,
                        ", ".join(f"{i.table_name}.{i.column_name}" for i in c.inputs),
                        c.rule_text,
                        c.sql_expression,
                    ]
                    for c in columns
                ]

            if view.branches:
                for branch in view.branches:
                    blocks.append(Heading(3, f"Branch: {branch.name}"))
                    details = [f"Driving input: {branch.driving_input}"]
                    for label, value in (
                        ("Joins", branch.joins),
                        ("Filters", branch.filters),
                        ("Group by", branch.group_by),
                    ):
                        if value:
                            details.append(f"{label}: {value}")
                    blocks.append(Bullets(details))
                    blocks.append(Table(["Column", "Mapping", *header[2:]], rows(branch.columns)))
            else:
                blocks.append(Table(["Column", "Mapping", *header[2:]], rows(view.columns)))
        return Page(f"mappings/{layer}.md", f"Mappings: {layer} Layer", blocks)

    @staticmethod
    def _kpi_page(
        systems: list[SourceSystem], kpis: list[Kpi], all_links: dict[uuid.UUID, list[KpiLink]]
    ) -> Page:
        codes = {s.id: s.code for s in systems}
        blocks: list[Block] = [Heading(1, "KPIs")]
        if not kpis:
            blocks.append(Paragraph("No KPIs are documented."))
        else:
            rows = []
            for k in kpis:
                links = all_links[k.id]
                rows.append(
                    [
                        k.name,
                        "Data Warehouse"
                        if k.source_system_id is None
                        else codes.get(k.source_system_id, ""),
                        k.definition,
                        k.formula_text,
                        k.unit,
                        k.aggregation,
                        k.owner,
                        k.status,
                        ", ".join(
                            f"{link.layer}.{link.table_name}.{link.column_name}" for link in links
                        ),
                    ]
                )
            blocks.append(
                Table(
                    [
                        "KPI",
                        "Scope",
                        "Definition",
                        "Formula",
                        "Unit",
                        "Aggregation",
                        "Owner",
                        "Status",
                        "DW columns",
                    ],
                    rows,
                )
            )
        return Page("kpis.md", "KPIs", blocks)

    @staticmethod
    def _lineage_page(
        views: dict[str, list[Any]], kpis: list[Kpi], all_links: dict[uuid.UUID, list[KpiLink]]
    ) -> Page:
        """Which tables feed each Core and Mart table, and which DW columns each KPI uses,
        from the saved mappings and KPI links."""
        blocks: list[Block] = [Heading(1, "Lineage summary")]
        rows: list[list[str]] = []
        for layer in MAPPING_LAYERS:
            for view in sorted(views[layer], key=lambda v: v.table_name.lower()):
                columns = [*view.columns, *(c for b in view.branches for c in b.columns)]
                feeders = sorted({i.table_name for c in columns for i in c.inputs}, key=str.lower)
                mapped = sum(1 for c in view.columns if c.inputs)
                rows.append(
                    [
                        layer,
                        view.table_name,
                        view.source_layer,
                        ", ".join(feeders),
                        f"{mapped} of {len(view.columns)}",
                    ]
                )
        if rows:
            blocks.append(Heading(2, "Tables"))
            blocks.append(
                Table(
                    ["Layer", "Table", "Built from", "Fed by tables", "Columns with inputs"], rows
                )
            )
        else:
            blocks.append(Paragraph("No Core or Mart mappings yet."))
        linked = [(k, all_links[k.id]) for k in kpis if all_links[k.id]]
        if linked:
            blocks.append(Heading(2, "KPIs"))
            blocks.append(
                Table(
                    ["KPI", "Uses DW columns"],
                    [
                        [
                            k.name,
                            ", ".join(f"{x.layer}.{x.table_name}.{x.column_name}" for x in links),
                        ]
                        for k, links in linked
                    ],
                )
            )
        return Page("lineage.md", "Lineage summary", blocks)
