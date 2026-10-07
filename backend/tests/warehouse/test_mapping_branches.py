"""Mapping branches, branch coverage and aggregates (spec §6.14, story 102)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from tests.roles import RoleClients
from tests.warehouse.test_column_mappings import base, column, get_mapping, mapping_path, table


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


@pytest.fixture
def model(warehouse: RoleClients) -> dict:
    """Core ``crm_customer`` and ``cb_customer`` feeding Mart ``dim_customer`` (name, email)."""
    crm = table(warehouse, "crm_customer", "core")
    cb = table(warehouse, "cb_customer", "core")
    mart = table(warehouse, "dim_customer", "mart")
    return {
        "mart": mart,
        "crm_id": column(warehouse, crm["id"], "customer_id"),
        "crm_name": column(warehouse, crm["id"], "full_name"),
        "cb_id": column(warehouse, cb["id"], "customer_id"),
        "cb_name": column(warehouse, cb["id"], "full_name"),
        "cb_email": column(warehouse, cb["id"], "email"),
        "key": column(warehouse, mart["id"], "customer_id"),
        "name": column(warehouse, mart["id"], "name"),
        "email": column(warehouse, mart["id"], "email"),
    }


def add_branch(roles, model, role="editor", **body):
    body = {"name": "CRM", "driving_input": "crm_customer"} | body
    return roles.client(role).post(
        f"{mapping_path(roles, model['mart']['id'])}/branches", json=body
    )


def put_branch_column(roles, model, branch_id, target, role="editor", **body):
    path = (
        f"{mapping_path(roles, model['mart']['id'])}/branches/{branch_id}"
        f"/columns/{model[target]['id']}"
    )
    return roles.client(role).put(path, json=body)


def two_branches(roles, model) -> tuple[str, str]:
    crm = add_branch(roles, model).json()["branches"][0]["id"]
    cb = add_branch(roles, model, name="Core Banking", driving_input="cb_customer").json()[
        "branches"
    ][1]["id"]
    put_branch_column(
        roles, model, crm, "key", mapping_type="direct", sql_expression="crm_customer.customer_id"
    )
    put_branch_column(
        roles, model, crm, "name", mapping_type="direct", sql_expression="crm_customer.full_name"
    )
    put_branch_column(roles, model, crm, "email", mapping_type="not_in_branch")
    put_branch_column(
        roles, model, cb, "key", mapping_type="direct", sql_expression="cb_customer.customer_id"
    )
    put_branch_column(
        roles, model, cb, "name", mapping_type="direct", sql_expression="cb_customer.full_name"
    )
    put_branch_column(
        roles, model, cb, "email", mapping_type="direct", sql_expression="cb_customer.email"
    )
    return crm, cb


def test_branches_are_numbered_in_order_and_carry_their_sql(warehouse, model):
    first = add_branch(
        warehouse, model, filters="crm_customer.customer_id > 0", group_by=None, having=None
    )
    second = add_branch(warehouse, model, name="Core Banking", driving_input="cb_customer")

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    branches = get_mapping(warehouse, model["mart"]["id"])["branches"]
    assert [(b["ordinal"], b["name"]) for b in branches] == [(1, "CRM"), (2, "Core Banking")]
    assert branches[0]["filters"] == "crm_customer.customer_id > 0"
    assert branches[0]["version"] == 1


def test_a_branch_is_changed_with_a_version_and_deleted(warehouse, model):
    branch = add_branch(warehouse, model).json()["branches"][0]
    path = f"{mapping_path(warehouse, model['mart']['id'])}/branches/{branch['id']}"

    changed = warehouse.client("editor").patch(path, json={"version": 1, "name": "CRM system"})
    stale = warehouse.client("editor").patch(path, json={"version": 1, "name": "x"})
    deleted = warehouse.client("editor").delete(path)

    assert changed.status_code == 200, changed.text
    assert changed.json()["branches"][0]["name"] == "CRM system"
    assert stale.status_code == 409
    assert deleted.status_code == 204
    assert get_mapping(warehouse, model["mart"]["id"])["branches"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"driving_input": "no_such_table"},
        {"driving_input": "dim_customer"},
        {"driving_input": "crm_customer", "filters": "customer_id >"},
        {"driving_input": "crm_customer", "joins": "JOIN nowhere ON 1 = 1"},
        {"name": ""},
    ],
)
def test_a_branch_must_read_tables_of_the_layer_below(warehouse, model, body):
    response = add_branch(warehouse, model, **body)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_mapping"
    assert get_mapping(warehouse, model["mart"]["id"])["branches"] == []


def test_a_branch_column_mapping_derives_its_own_edges(warehouse, model):
    crm, _ = two_branches(warehouse, model)

    mapping = get_mapping(warehouse, model["mart"]["id"])

    by_branch = {b["id"]: b for b in mapping["branches"]}
    name = next(c for c in by_branch[crm]["columns"] if c["column_name"] == "name")
    assert name["inputs"][0]["table_name"] == "crm_customer"
    email = next(c for c in by_branch[crm]["columns"] if c["column_name"] == "email")
    assert email["mapping_type"] == "not_in_branch"
    up = warehouse.client("viewer").get(f"{base(warehouse)}/lineage/columns/{model['name']['id']}")
    assert {e["from_label"] for e in up.json()["edges"]} == {
        "crm_customer.full_name",
        "cb_customer.full_name",
    }


def uses_labels(roles, model) -> set[str]:
    up = roles.client("viewer").get(f"{base(roles)}/lineage/columns/{model['name']['id']}")
    return {e["from_label"] for e in up.json()["edges"] if e["kind"] == "uses"}


def test_branch_joins_and_filters_derive_uses_edges_replaced_on_every_save(warehouse, model):
    created = add_branch(
        warehouse,
        model,
        joins="JOIN cb_customer b ON b.customer_id = crm_customer.customer_id",
        filters="crm_customer.full_name <> ''",
    )
    branch = created.json()["branches"][0]
    path = f"{mapping_path(warehouse, model['mart']['id'])}/branches/{branch['id']}"

    assert uses_labels(warehouse, model) == {
        "cb_customer.customer_id",
        "crm_customer.customer_id",
        "crm_customer.full_name",
    }
    changed = warehouse.client("editor").patch(
        path, json={"version": 1, "joins": "", "filters": "", "group_by": "crm_customer.full_name"}
    )
    assert changed.status_code == 200, changed.text
    assert uses_labels(warehouse, model) == {"crm_customer.full_name"}
    assert warehouse.client("editor").delete(path).status_code == 204
    assert uses_labels(warehouse, model) == set()


def test_a_branch_condition_must_read_columns_of_the_layer_below(warehouse, model):
    response = add_branch(warehouse, model, filters="crm_customer.nope = 1")

    assert response.status_code == 422, response.text
    assert get_mapping(warehouse, model["mart"]["id"])["branches"] == []


def test_not_in_branch_only_applies_inside_a_branch_and_carries_no_sql(warehouse, model):
    branch = add_branch(warehouse, model).json()["branches"][0]["id"]
    table_level = warehouse.client("editor").put(
        f"{mapping_path(warehouse, model['mart']['id'])}/columns/{model['email']['id']}",
        json={"mapping_type": "not_in_branch"},
    )
    with_sql = put_branch_column(
        warehouse, model, branch, "email", mapping_type="not_in_branch", sql_expression="1"
    )

    assert table_level.status_code == 422
    assert with_sql.status_code == 422


def test_system_columns_are_not_mapped_per_branch(warehouse, model):
    branch = add_branch(warehouse, model).json()["branches"][0]["id"]
    sk = next(
        c
        for c in get_mapping(warehouse, model["mart"]["id"])["columns"]
        if c["column_name"] == "dim_customer_key"
    )

    path = f"{mapping_path(warehouse, model['mart']['id'])}/branches/{branch}"
    response = warehouse.client("editor").put(
        f"{path}/columns/{sk['column_id']}",
        json={"mapping_type": "constant", "sql_expression": "1"},
    )

    assert response.status_code == 422, response.text


def coverage(roles, model) -> dict[str, dict]:
    mapping = get_mapping(roles, model["mart"]["id"])
    return {c["column_name"]: c for c in mapping["coverage"]}


def test_coverage_is_per_column_mapped_or_not_in_branch_in_every_branch(warehouse, model):
    _, cb = two_branches(warehouse, model)
    response = put_branch_column(warehouse, model, cb, "email", version=1, mapping_type="unmapped")
    assert response.status_code == 200, response.text

    result = coverage(warehouse, model)

    assert result["name"]["covered"] is True
    assert result["customer_id"]["covered"] is True
    assert result["email"]["covered"] is False
    assert result["email"]["missing_branch_ids"] == [cb]
    assert result["dim_customer_key"]["system"] is True
    assert result["dim_customer_key"]["covered"] is True


def test_a_branch_with_no_mapping_for_a_column_leaves_it_uncovered(warehouse, model):
    add_branch(warehouse, model)

    assert coverage(warehouse, model)["name"]["covered"] is False


def test_integration_rule_and_match_keys_sit_on_the_table_mapping(warehouse, model):
    response = warehouse.client("editor").patch(
        mapping_path(warehouse, model["mart"]["id"]),
        json={
            "version": 0,
            "integration_rule": "Customers in both systems are merged, CRM wins",
            "match_keys": ["email"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["match_keys"] == ["email"]
    assert response.json()["integration_rule"].startswith("Customers in both")


def test_a_two_branch_dim_customer_keeps_rows_present_in_only_one_system(warehouse, model):
    two_branches(warehouse, model)
    mapping = get_mapping(warehouse, model["mart"]["id"])
    engine = warehouse.app.state.engine
    with engine.begin() as connection:
        connection.execute(sa.text("create table crm_customer (customer_id int, full_name text)"))
        connection.execute(
            sa.text("create table cb_customer (customer_id int, full_name text, email text)")
        )
        connection.execute(sa.text("insert into crm_customer values (1, 'Ann'), (2, 'Bob')"))
        connection.execute(
            sa.text("insert into cb_customer values (2, 'Bob', 'b@x'), (3, 'Cy', 'c@x')")
        )
        rows = connection.execute(sa.text(mapping["sql"] + " ORDER BY 1, 3 NULLS FIRST")).all()
        connection.execute(sa.text("drop table crm_customer, cb_customer"))

    assert "UNION ALL" in mapping["sql"]
    assert [tuple(r) for r in rows] == [
        (1, "Ann", None),
        (2, "Bob", None),
        (2, "Bob", "b@x"),
        (3, "Cy", "c@x"),
    ]


@pytest.fixture
def aggregate(warehouse: RoleClients) -> dict:
    """Mart ``fact_sales_monthly`` (aggregate) fed by Core ``sales``."""
    sales = table(warehouse, "sales", "core", kind="fact")
    mart = table(warehouse, "fact_sales_monthly", "mart", kind="fact")
    response = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{mart['id']}", json={"is_aggregate": True, "version": 1}
    )
    assert response.status_code == 200, response.text
    return {
        "mart": mart,
        "region": column(warehouse, sales["id"], "region"),
        "amount": column(warehouse, sales["id"], "amount"),
        "t_region": column(warehouse, mart["id"], "region"),
        "t_total": column(warehouse, mart["id"], "total_amount"),
    }


def aggregate_branch(roles, aggregate, **body) -> str:
    path = f"{mapping_path(roles, aggregate['mart']['id'])}/branches"
    body = {"name": "All", "driving_input": "sales"} | body
    response = roles.client("editor").post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()["branches"][0]["id"]


def map_aggregate(roles, aggregate, branch, target, sql):
    path = (
        f"{mapping_path(roles, aggregate['mart']['id'])}/branches/{branch}"
        f"/columns/{aggregate[target]['id']}"
    )
    response = roles.client("editor").put(
        path, json={"mapping_type": "derived", "sql_expression": sql}
    )
    assert response.status_code == 200, response.text


def branch_errors(roles, aggregate) -> list[dict]:
    return get_mapping(roles, aggregate["mart"]["id"])["branches"][0]["errors"]


def test_an_aggregate_with_every_plain_output_in_group_by_is_valid(warehouse, aggregate):
    branch = aggregate_branch(warehouse, aggregate, group_by="sales.region")
    map_aggregate(warehouse, aggregate, branch, "t_region", "sales.region")
    map_aggregate(warehouse, aggregate, branch, "t_total", "SUM(sales.amount)")

    assert branch_errors(warehouse, aggregate) == []


def test_a_plain_output_missing_from_group_by_is_reported(warehouse, aggregate):
    branch = aggregate_branch(warehouse, aggregate, group_by="sales.amount")
    map_aggregate(warehouse, aggregate, branch, "t_region", "sales.region")
    map_aggregate(warehouse, aggregate, branch, "t_total", "SUM(sales.amount)")

    errors = branch_errors(warehouse, aggregate)

    assert [(e["code"], e["column_name"]) for e in errors] == [("not_in_group_by", "region")]


def test_an_aggregate_branch_without_group_by_reports_its_plain_outputs(warehouse, aggregate):
    branch = aggregate_branch(warehouse, aggregate)
    map_aggregate(warehouse, aggregate, branch, "t_region", "UPPER(sales.region)")

    assert [e["column_name"] for e in branch_errors(warehouse, aggregate)] == ["region"]


def test_a_non_aggregate_table_is_not_checked_for_group_by(warehouse, model):
    branch = add_branch(warehouse, model).json()["branches"][0]["id"]
    put_branch_column(
        warehouse,
        model,
        branch,
        "name",
        mapping_type="direct",
        sql_expression="crm_customer.full_name",
    )

    assert get_mapping(warehouse, model["mart"]["id"])["branches"][0]["errors"] == []


def test_viewers_read_branches_but_cannot_change_them(warehouse, model):
    denied = add_branch(warehouse, model, role="viewer")

    assert denied.status_code == 403
    assert get_mapping(warehouse, model["mart"]["id"])["branches"] == []
