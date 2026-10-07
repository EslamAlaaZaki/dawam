"""Mapping validation, type warnings and coverage (spec §6.14, stories 104-106)."""

from __future__ import annotations

import pytest

from tests.roles import RoleClients
from tests.warehouse.test_column_mappings import base, mapping_path, table
from tests.warehouse.test_mapping_branches import add_branch, put_branch_column


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def col(roles, table_id, name, type_="string", nullable=None, **type_extra) -> dict:
    body = {"name": name, "data_type": {"type": type_, **type_extra}, "role": "attribute"}
    if nullable is not None:
        body["is_nullable"] = nullable
    response = roles.client("editor").post(f"{base(roles)}/tables/{table_id}/columns", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def put(roles, table_id, column, **body):
    path = f"{mapping_path(roles, table_id)}/columns/{column['id']}"
    response = roles.client("editor").put(path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def validation(roles, role="viewer") -> dict:
    response = roles.client(role).get(f"{base(roles)}/validation")
    assert response.status_code == 200, response.text
    return response.json()


def coverage(roles) -> dict:
    response = roles.client("viewer").get(f"{base(roles)}/coverage")
    assert response.status_code == 200, response.text
    return response.json()


def codes(report: dict) -> list[tuple[str, str]]:
    return [(p["severity"], p["code"]) for p in report["problems"]]


@pytest.fixture
def model(warehouse: RoleClients) -> dict:
    """Core ``customer`` (long ``name`` string(200), ``total`` bigint) feeding Mart
    ``dim_customer`` (``name`` string(50), ``total`` integer, ``code`` string(20))."""
    core = table(warehouse, "customer", "core")
    mart = table(warehouse, "dim_customer", "mart")
    return {
        "core": core,
        "mart": mart,
        "src_name": col(warehouse, core["id"], "name", length=200),
        "src_total": col(warehouse, core["id"], "total", "bigint"),
        "name": col(warehouse, mart["id"], "name", length=50),
        "total": col(warehouse, mart["id"], "total", "integer"),
        "code": col(warehouse, mart["id"], "code", length=20),
    }


def test_validation_needs_a_data_warehouse(roles):
    response = roles.client("viewer").get(f"{base(roles)}/validation")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_set_up"


def test_a_direct_mapping_that_may_truncate_gets_a_warning(warehouse, model):
    put(
        warehouse,
        model["mart"]["id"],
        model["name"],
        mapping_type="direct",
        sql_expression="customer.name",
    )
    put(
        warehouse,
        model["mart"]["id"],
        model["total"],
        mapping_type="direct",
        sql_expression="customer.total",
    )

    report = validation(warehouse)

    truncations = [p for p in report["problems"] if p["code"] == "may_truncate"]
    assert {p["column_name"] for p in truncations} == {"name", "total"}
    assert all(
        p["severity"] == "warning" and p["table_name"] == "dim_customer" for p in truncations
    )
    assert report["error_count"] == 0


def test_a_fitting_mapping_has_no_type_warning(warehouse, model):
    mart_id = model["mart"]["id"]
    # string(200) into string(20) warns; a derived expression's type is unknown, so no warning
    put(warehouse, mart_id, model["code"], mapping_type="direct", sql_expression="customer.name")
    put(
        warehouse,
        mart_id,
        model["name"],
        mapping_type="derived",
        sql_expression="upper(customer.name)",
    )

    report = validation(warehouse)

    assert [p["column_name"] for p in report["problems"] if p["code"] == "may_truncate"] == ["code"]


def test_a_nullable_input_into_a_required_column_warns(warehouse, model):
    core, mart = model["core"], model["mart"]
    required = col(warehouse, mart["id"], "email", nullable=False)
    source = col(warehouse, core["id"], "email", nullable=True)
    put(warehouse, mart["id"], required, mapping_type="direct", sql_expression="customer.email")
    assert source["is_nullable"]

    assert ("warning", "nullable_into_required") in codes(validation(warehouse))


def test_unparsable_sql_is_an_error(warehouse, model):
    put(
        warehouse,
        model["mart"]["id"],
        model["name"],
        mapping_type="derived",
        sql_expression="upper((customer.name",
    )

    report = validation(warehouse)

    assert ("error", "unparsed_sql") in codes(report)
    assert report["error_count"] == 1
    assert report["problems"][0]["severity"] == "error"  # errors first


def test_unmapped_columns_are_warnings_and_a_branchy_table_needs_an_integration_rule(
    warehouse, model
):
    add_branch(warehouse, model, driving_input="customer")
    add_branch(warehouse, model, name="Other", driving_input="customer")

    report = validation(warehouse)

    assert ("warning", "unmapped_column") in codes(report)
    assert ("warning", "missing_integration_rule") in codes(report)
    unmapped = next(
        p
        for p in report["problems"]
        if p["code"] == "unmapped_column" and p["table_name"] == "dim_customer"
    )
    assert "CRM" in unmapped["message"]


def test_coverage_is_counted_per_table_layer_and_warehouse(warehouse, model):
    put(
        warehouse,
        model["mart"]["id"],
        model["name"],
        mapping_type="direct",
        sql_expression="customer.name",
    )

    report = coverage(warehouse)

    layers = {layer["layer"]: layer for layer in report["layers"]}
    # Core ``customer`` is not mapped from Staging: its two columns are uncovered too.
    assert layers["core"]["coverage"] == {"total": 2, "covered": 0, "percent": 0}
    assert layers["mart"]["coverage"] == {"total": 3, "covered": 1, "percent": 33}
    mart_table = layers["mart"]["tables"][0]
    assert mart_table["table_name"] == "dim_customer"
    assert mart_table["coverage"]["covered"] == 1
    assert report["coverage"] == {"total": 5, "covered": 1, "percent": 20}


def test_coverage_is_branch_aware(warehouse, model):
    first = add_branch(warehouse, model, driving_input="customer").json()["branches"][0]["id"]
    second = add_branch(warehouse, model, name="Other", driving_input="customer").json()[
        "branches"
    ][1]["id"]
    for target in ("name", "total", "code"):
        put_branch_column(warehouse, model, first, target, mapping_type="not_in_branch")
    put_branch_column(warehouse, model, second, "name", mapping_type="not_in_branch")

    mart = {
        t["table_name"]: t["coverage"]
        for layer in coverage(warehouse)["layers"]
        for t in layer["tables"]
    }["dim_customer"]

    assert mart == {"total": 3, "covered": 1, "percent": 33}  # only ``name`` is in both


def test_a_layer_with_everything_covered_completes_dw_modeling_progress(warehouse, model):
    progress_path = f"/api/v1/workspaces/{warehouse.workspace_id}/progress"

    def status(layer: str) -> str:
        items = warehouse.client("viewer").get(progress_path).json()["dw_modeling"]
        return next(i["status"] for i in items if i["layer"] == layer)

    assert (status("core"), status("mart")) == ("in_progress", "in_progress")
    for target, expression in (
        ("name", "customer.name"),
        ("total", "customer.total"),
        ("code", "customer.name"),
    ):
        put(
            warehouse,
            model["mart"]["id"],
            model[target],
            mapping_type="derived",
            sql_expression=f"cast({expression} as text)",
        )

    assert status("mart") == "complete"


def test_coverage_percent_rounds_down(warehouse):
    from dawam.modules.warehouse.validation_service import _coverage

    assert _coverage(200, 199).percent == 99


def test_modeling_progress_needs_mappable_columns_and_no_unparsable_mapping(warehouse):
    progress_path = f"/api/v1/workspaces/{warehouse.workspace_id}/progress"

    def status(layer: str) -> str:
        items = warehouse.client("viewer").get(progress_path).json()["dw_modeling"]
        return next(i["status"] for i in items if i["layer"] == layer)

    core = table(warehouse, "customer", "core")
    mart = table(warehouse, "dim_customer", "mart")
    # Only system columns: nothing to map, so the Layer is not complete.
    assert (status("core"), status("mart")) == ("in_progress", "in_progress")
    src = col(warehouse, core["id"], "name")
    target = col(warehouse, mart["id"], "name")
    put(warehouse, mart["id"], target, mapping_type="direct", sql_expression="customer.name")
    assert status("mart") == "complete"
    put(
        warehouse,
        mart["id"],
        target,
        version=1,
        mapping_type="derived",
        sql_expression="upper((customer.name",
    )
    assert status("mart") == "in_progress"
    assert src["name"] == "name"


def test_the_validation_report_is_open_to_every_member(warehouse, model):
    for role in ("owner", "editor", "viewer"):
        assert validation(warehouse, role)["coverage"]["coverage"]["total"] == 5
