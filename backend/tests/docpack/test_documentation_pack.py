"""Documentation pack export (spec §6.15, story 128): zipped Markdown and one HTML file,
downloaded or saved into the file area, also from an archived Workspace.

Behaviour is driven through the HTTP API.
"""

from __future__ import annotations

import io
import zipfile

from tests.roles import RoleClients
from tests.warehouse.test_column_mappings import base, mapping_path
from tests.warehouse.test_mapping_branches import model, warehouse

__all__ = ["model", "warehouse"]


def pack_url(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/documentation-pack"


def pages(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name).decode() for name in archive.namelist()}


def documented(roles: RoleClients, model) -> None:
    """A Source System, a described Core column, a mapping and a KPI linked to a Mart column."""
    system = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems",
        json={"name": "Core Banking", "code": "cbs"},
    )
    assert system.status_code == 201, system.text
    editor = roles.client("editor")
    response = editor.put(
        f"{mapping_path(roles, model['mart']['id'])}/columns/{model['name']['id']}",
        json={
            "mapping_type": "derived",
            "rule_text": "Use the Core full name",
            "sql_expression": "crm_customer.full_name",
        },
    )
    assert response.status_code == 200, response.text
    kpi = editor.post(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis",
        json={"name": "Active customers", "definition": "Customers with | a pipe"},
    )
    assert kpi.status_code == 201, kpi.text
    put = editor.put(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis/{kpi.json()['id']}/links",
        json={"version": kpi.json()["version"], "dw_column_ids": [model["name"]["id"]]},
    )
    assert put.status_code == 200, put.text


def test_a_member_downloads_the_pack_as_zipped_markdown(warehouse, model):
    documented(warehouse, model)

    response = warehouse.client("viewer").get(pack_url(warehouse))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "documentation-pack.zip" in response.headers["content-disposition"]
    files = pages(response.content)
    assert set(files) == {
        "README.md",
        "source-schemas/cbs.md",
        "dw-schema/staging.md",
        "dw-schema/core.md",
        "dw-schema/mart.md",
        "mappings/core.md",
        "mappings/mart.md",
        "kpis.md",
        "lineage.md",
    }
    assert "No Snapshot has been taken yet." in files["source-schemas/cbs.md"]
    assert "dim_customer" in files["dw-schema/mart.md"]
    assert "crm_customer" in files["dw-schema/core.md"]
    assert "Use the Core full name" in files["mappings/mart.md"]
    assert "Active customers" in files["kpis.md"]
    assert "Customers with \\| a pipe" in files["kpis.md"]
    assert "mart.dim_customer.name" in files["lineage.md"]
    assert "crm_customer" in files["lineage.md"]


def test_a_member_downloads_the_pack_as_one_html_file(warehouse, model):
    documented(warehouse, model)

    response = warehouse.client("viewer").get(pack_url(warehouse), params={"format": "html"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "documentation-pack.html" in response.headers["content-disposition"]
    html = response.text
    assert html.startswith("<!doctype html>")
    for text in ("Active customers", "dim_customer", "Use the Core full name"):
        assert text in html
    assert "<script" not in html


def test_the_pack_works_before_the_data_warehouse_is_set_up(roles: RoleClients):
    response = roles.client("viewer").get(pack_url(roles))

    assert response.status_code == 200, response.text
    assert "The Data Warehouse is not set up yet." in pages(response.content)["README.md"]


def test_an_unknown_format_is_refused(roles: RoleClients):
    response = roles.client("viewer").get(pack_url(roles), params={"format": "pdf"})

    assert response.status_code == 422


def test_an_editor_saves_the_pack_as_markdown_into_the_file_area(warehouse, model):
    documented(warehouse, model)
    url = f"{base(warehouse)}/files/documentation-pack"

    saved = warehouse.client("editor").post(url)

    assert saved.status_code == 201, saved.text
    assert saved.json()["name"] == "documentation-pack.md"
    listed = warehouse.client("viewer").get(f"{base(warehouse)}/files").json()["items"]
    assert "documentation-pack.md" in [f["name"] for f in listed]
    download = warehouse.client("viewer").get(
        f"/api/v1/workspaces/{warehouse.workspace_id}/files/{saved.json()['id']}/download"
    )
    assert download.text.startswith("# Documentation pack")
    assert "Active customers" in download.text
    assert warehouse.client("editor").post(url).status_code == 201  # overwritten in place


def test_saving_needs_an_editor(warehouse):
    url = f"{base(warehouse)}/files/documentation-pack"
    assert warehouse.client("viewer").post(url).status_code == 403


def test_saving_before_the_warehouse_is_set_up_is_not_found(roles: RoleClients):
    url = f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse/files/documentation-pack"

    assert roles.client("editor").post(url).status_code == 404


def test_an_archived_workspace_still_exports_but_refuses_saving(warehouse, model):
    documented(warehouse, model)
    warehouse.client("viewer")  # members are added lazily, which an archived Workspace refuses
    archived = warehouse.client("owner").post(
        f"/api/v1/workspaces/{warehouse.workspace_id}/archive"
    )
    assert archived.status_code == 204, archived.text

    zipped = warehouse.client("viewer").get(pack_url(warehouse))
    html = warehouse.client("viewer").get(pack_url(warehouse), params={"format": "html"})
    saved = warehouse.client("editor").post(f"{base(warehouse)}/files/documentation-pack")

    assert zipped.status_code == 200 and "kpis.md" in pages(zipped.content)
    assert html.status_code == 200
    assert saved.status_code == 409
    assert saved.json()["error"]["code"] == "workspace_archived"
