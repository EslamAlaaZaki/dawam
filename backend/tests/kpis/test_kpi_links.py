"""KPI links and formula SQL against the DW Schema (spec §6.13, stories 76a, 78, 80)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from dawam.modules.audit import AuditService
from dawam.modules.changesets import ChangeSetService, ProposedItem
from dawam.modules.kpis import KpiService
from dawam.modules.kpis.api import kpi_service as build
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.errors import ApiError
from tests.kpis.test_kpis import add, created, error_code, kpis_path
from tests.roles import RoleClients


def dw(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


def table(roles: RoleClients, name: str, layer: str, kind="dimension") -> dict:
    body = {"layer": layer, "name": name, "kind": kind}
    if kind == "fact":
        body |= {"grain": "One row per loan", "fact_type": "transactional"}
    response = roles.client("editor").post(f"{dw(roles)}/tables", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def column(roles: RoleClients, table_id: str, name: str, role="attribute", **extra) -> dict:
    body = {"name": name, "data_type": {"type": "decimal", "precision": 18, "scale": 2}} | extra
    response = roles.client("editor").post(
        f"{dw(roles)}/tables/{table_id}/columns", json={**body, "role": role}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def model(roles: RoleClients) -> dict:
    """Core ``fact_loan`` (amount, rate), Mart ``mart_loan`` (amount_m), a Staging-free DW."""
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    core = table(roles, "fact_loan", "core", "fact")
    mart = table(roles, "mart_loan", "mart", "fact")
    return {
        "core": core,
        "mart": mart,
        "amount": column(roles, core["id"], "amount", "measure", additivity="additive"),
        "rate": column(roles, core["id"], "rate", "measure", additivity="non_additive"),
        "amount_m": column(roles, mart["id"], "amount_m", "measure", additivity="additive"),
    }


def links_path(roles: RoleClients, kpi: dict) -> str:
    return f"{kpis_path(roles)}/{kpi['id']}/links"


def put_links(
    roles: RoleClients, kpi: dict, column_ids: list[str], *, version=None, as_role="editor"
):
    return roles.client(as_role).put(
        links_path(roles, kpi),
        json={"version": version or kpi["version"], "dw_column_ids": column_ids},
    )


def test_an_editor_links_a_kpi_to_dw_columns(roles: RoleClients, model):
    kpi = created(roles)

    response = put_links(roles, kpi, [model["rate"]["id"], model["amount"]["id"]])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2
    assert [(i["layer"], i["table_name"], i["column_name"]) for i in body["items"]] == [
        ("core", "fact_loan", "amount"),
        ("core", "fact_loan", "rate"),
    ]
    listed = roles.client("viewer").get(links_path(roles, kpi))
    assert listed.status_code == 200
    assert listed.json() == body
    assert roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}").json()["version"] == 2


def test_links_are_replaced_and_can_be_cleared(roles: RoleClients, model):
    kpi = created(roles)
    put_links(roles, kpi, [model["amount"]["id"], model["rate"]["id"]])

    replaced = put_links(roles, kpi, [model["rate"]["id"]], version=2)
    unchanged = put_links(roles, kpi, [model["rate"]["id"]], version=3)
    cleared = put_links(roles, kpi, [], version=3)

    assert [i["column_name"] for i in replaced.json()["items"]] == ["rate"]
    assert unchanged.json()["version"] == 3  # nothing changed: no new version
    assert cleared.json() == {"version": 4, "items": []}


def test_a_stale_version_is_a_conflict(roles: RoleClients, model):
    kpi = created(roles)
    put_links(roles, kpi, [model["amount"]["id"]])

    stale = put_links(roles, kpi, [], version=1)

    assert stale.status_code == 409
    assert error_code(stale) == "version_conflict"


def test_links_are_edges_of_the_lineage_graph(roles: RoleClients, model):
    kpi = created(roles)
    put_links(roles, kpi, [model["amount"]["id"]])

    response = roles.client("viewer").get(
        f"{dw(roles)}/lineage/columns/{model['amount']['id']}", params={"direction": "downstream"}
    )

    edges = response.json()["edges"]
    assert [(e["kind"], e["from_label"], e["to_type"], e["to_id"]) for e in edges] == [
        ("kpi", "fact_loan.amount", "kpi", kpi["id"])
    ]
    put_links(roles, kpi, [], version=2)
    again = roles.client("viewer").get(
        f"{dw(roles)}/lineage/columns/{model['amount']['id']}", params={"direction": "downstream"}
    )
    assert again.json()["edges"] == []


def test_deleting_a_kpi_or_a_column_drops_the_links(roles: RoleClients, model):
    gone = created(roles, name="Gone")
    kept = created(roles, name="Kept")
    put_links(roles, gone, [model["amount"]["id"]])
    put_links(roles, kept, [model["amount"]["id"], model["rate"]["id"]])

    roles.client("editor").delete(f"{kpis_path(roles)}/{gone['id']}")
    deleted = roles.client("editor").delete(
        f"{dw(roles)}/tables/{model['core']['id']}/columns/{model['rate']['id']}"
    )

    assert deleted.status_code == 204, deleted.text
    assert [
        i["column_name"]
        for i in roles.client("viewer").get(links_path(roles, kept)).json()["items"]
    ] == ["amount"]
    edges = (
        roles.client("viewer")
        .get(
            f"{dw(roles)}/lineage/columns/{model['amount']['id']}",
            params={"direction": "downstream"},
        )
        .json()["edges"]
    )
    assert [e["to_id"] for e in edges] == [kept["id"]]


def test_links_must_be_core_or_mart_columns_of_the_warehouse(roles: RoleClients, model):
    kpi = created(roles)
    unknown = put_links(roles, kpi, [str(uuid.uuid4())])

    assert unknown.status_code == 422
    assert error_code(unknown) == "invalid_kpi_link"


def test_links_need_a_data_warehouse(roles: RoleClients):
    kpi = created(roles)

    response = put_links(roles, kpi, [str(uuid.uuid4())])

    assert response.status_code == 404
    assert error_code(response) == "not_set_up"


def test_a_kpi_links_to_the_highest_layer_holding_the_measure(roles: RoleClients, model):
    kpi = created(roles)
    mapped = roles.client("editor").put(
        f"{dw(roles)}/tables/{model['mart']['id']}/mapping/columns/{model['amount_m']['id']}",
        json={
            "mapping_type": "direct",
            "rule_text": "As is",
            "sql_expression": "fact_loan.amount",
        },
    )
    assert mapped.status_code == 200, mapped.text

    core = put_links(roles, kpi, [model["amount"]["id"]])
    mart = put_links(roles, kpi, [model["amount_m"]["id"]])

    assert core.status_code == 422
    assert error_code(core) == "invalid_kpi_link"
    assert "mart_loan.amount_m" in core.json()["error"]["message"]
    assert mart.status_code == 200, mart.text


def test_only_editors_link(roles: RoleClients, model):
    kpi = created(roles)

    assert put_links(roles, kpi, [], as_role="viewer").status_code == 403
    assert put_links(roles, kpi, [], as_role="owner").status_code == 200


def test_link_changes_are_audited(roles: RoleClients, model, app, clock):
    kpi = created(roles)
    clock.advance(timedelta(minutes=1))
    put_links(roles, kpi, [model["amount"]["id"]])
    clock.advance(timedelta(minutes=1))
    put_links(roles, kpi, [model["rate"]["id"]], version=2)

    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
    )

    _, first, second = entries
    assert first.old == {"links": []}
    assert first.new == {"links": [model["amount"]["id"]]}
    assert second.old == {"links": [model["amount"]["id"]]}
    assert second.new == {"links": [model["rate"]["id"]]}


def test_a_structural_edit_returns_an_approved_kpi_to_draft(roles: RoleClients, model):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"
    approve = roles.client("owner").patch(path, json={"version": 1, "status": "approved"})
    assert approve.json()["status"] == "approved"

    text = roles.client("editor").patch(path, json={"version": 2, "definition": "Reworded"})
    linked = put_links(roles, kpi, [model["amount"]["id"]], version=3)
    after_links = roles.client("editor").get(path).json()
    roles.client("owner").patch(path, json={"version": 4, "status": "approved"})
    formula = roles.client("editor").patch(
        path, json={"version": 5, "formula_sql": "SELECT SUM(amount) FROM fact_loan"}
    )

    assert text.json()["status"] == "approved"
    assert linked.status_code == 200
    assert after_links["status"] == "draft"
    assert formula.json()["status"] == "draft"


def patch_formula(roles: RoleClients, kpi: dict, sql: str):
    return roles.client("editor").patch(
        f"{kpis_path(roles)}/{kpi['id']}", json={"version": kpi["version"], "formula_sql": sql}
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(amount) FROM fact_loan",
        "SELECT SUM(l.amount) / COUNT(*) FROM core.fact_loan AS l",
        "SELECT SUM(amount_m) AS total FROM mart_loan",
        "WITH t AS (SELECT amount FROM fact_loan) SELECT SUM(amount) FROM t",
        "SELECT SUM(fact_loan.amount * fact_loan.rate) FROM fact_loan",
        "SELECT 1",
        "SELECT 1 -- ; DROP TABLE fact_loan",  # a comment, not a second statement
    ],
)
def test_formula_sql_that_fits_the_dw_schema_is_accepted(roles: RoleClients, model, sql):
    kpi = created(roles)

    response = patch_formula(roles, kpi, sql)

    assert response.status_code == 200, response.text
    assert response.json()["formula_sql"] == sql


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ("SELECT SUM(amount) FROM fact_nope", "fact_nope"),
        ("SELECT SUM(nope) FROM fact_loan", "nope"),
        ("SELECT SUM(l.nope) FROM fact_loan l", "nope"),
        ("SELECT SUM(x.amount) FROM fact_loan l", "x"),
        ("SELECT SUM( FROM", "SQL"),
        ("DELETE FROM fact_loan", "SELECT"),
        ("SELECT 1; SELECT 2", "one"),
        ("SELECT amount_m FROM fact_loan", "amount_m"),
    ],
)
def test_formula_sql_that_does_not_fit_the_dw_schema_is_refused(
    roles: RoleClients, model, sql, fragment
):
    kpi = created(roles)

    response = patch_formula(roles, kpi, sql)
    on_create = add(roles, name="Other", formula_sql=sql)

    for refused in (response, on_create):
        assert refused.status_code == 422
        assert error_code(refused) == "invalid_kpi"
        assert refused.json()["error"]["details"]["field"] == "formula_sql"
        assert fragment in refused.json()["error"]["message"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE fact_loan",
        "SELECT 1 /* ; */; DELETE FROM fact_loan",
        "WITH d AS (DELETE FROM fact_loan RETURNING amount) SELECT SUM(amount) FROM d",
        "WITH i AS (INSERT INTO fact_loan (amount) VALUES (1) RETURNING amount) SELECT * FROM i",
        "WITH u AS (UPDATE fact_loan SET amount = 0 RETURNING amount) SELECT * FROM u",
        "SELECT amount INTO stolen FROM fact_loan",
        "SELECT amount FROM fact_loan FOR UPDATE",
        "INSERT INTO fact_loan (amount) VALUES (1)",
        "CREATE TABLE x AS SELECT amount FROM fact_loan",
        "DROP TABLE fact_loan",
        "TRUNCATE TABLE fact_loan",
        "COPY fact_loan TO '/tmp/x'",
    ],
)
@pytest.mark.parametrize("with_dw", [True, False])
def test_formula_sql_is_one_read_only_select(roles: RoleClients, request, sql, with_dw):
    if with_dw:
        request.getfixturevalue("model")
    kpi = created(roles)

    response = patch_formula(roles, kpi, sql)
    on_create = add(roles, name="Other", formula_sql=sql)

    for refused in (response, on_create):
        assert refused.status_code == 422, sql
        assert error_code(refused) == "invalid_kpi"


def test_before_the_dw_exists_formula_sql_is_only_parsed(roles: RoleClients):
    kpi = created(roles)

    broken = patch_formula(roles, kpi, "SELECT SUM( FROM")
    fine = patch_formula(roles, kpi, "SELECT SUM(x) FROM anything")

    assert broken.status_code == 422
    assert fine.status_code == 200


# --- the AI's Change Set ---------------------------------------------------------------


def engine(roles: RoleClients) -> ChangeSetService:
    state = roles.app.state
    clock = state.services.clock
    return ChangeSetService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        handlers=state.change_set_handlers,
        notifications=NotificationService(state.engine, clock=clock),
        clock=clock,
    )


def kpi_service(roles: RoleClients) -> KpiService:
    return build(SimpleNamespace(app=roles.app))  # type: ignore[arg-type]


def propose(roles: RoleClients, kpi: dict, **payload):
    return engine(roles).propose(
        roles.user("editor"),
        roles.workspace_id,
        origin="ai",
        scope={"kind": "kpi_links"},
        title="Link KPIs",
        items=[
            ProposedItem(
                key="k",
                object_type="kpi",
                operation="update",
                object_id=uuid.UUID(kpi["id"]),
                payload=payload,
                label=kpi["name"],
            )
        ],
    )


def accept(roles: RoleClients, change_set_id) -> dict:
    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets/{change_set_id}/accept", json={}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_candidates_are_the_kpis_without_links_or_formula_sql(roles: RoleClients, model):
    bare = created(roles, name="Bare", formula_text="Total loan amount")
    no_sql = created(roles, name="No SQL")
    put_links(roles, no_sql, [model["amount"]["id"]])
    no_links = created(roles, name="No links", formula_sql="SELECT 1")
    done = created(roles, name="Done", formula_sql="SELECT 1")
    put_links(roles, done, [model["amount"]["id"]])

    found = kpi_service(roles).link_candidates(roles.user("editor"), roles.workspace_id)

    assert {k.name for k in found.kpis} == {"Bare", "No SQL", "No links"}
    assert found.schema is not None
    assert {t.name for t in found.schema.tables} == {"fact_loan", "mart_loan"}
    assert bare["id"] and no_links["id"]


def test_an_accepted_change_set_links_the_kpi_and_writes_its_formula_sql(
    roles: RoleClients, model, app
):
    kpi = created(roles, formula_text="Total loan amount")
    proposed = propose(
        roles,
        kpi,
        formula_sql="SELECT SUM(amount) FROM fact_loan",
        links=[model["amount"]["id"]],
    )
    assert proposed.items[0].required_role == "editor"
    assert proposed.items[0].base_values == {"formula_sql": None, "links": []}

    result = accept(roles, proposed.change_set.id)

    assert result["change_set"]["change_set"]["status"] == "applied"
    applied = roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}").json()
    assert applied["formula_sql"] == "SELECT SUM(amount) FROM fact_loan"
    assert applied["version"] == 2
    assert [
        i["column_name"] for i in roles.client("viewer").get(links_path(roles, kpi)).json()["items"]
    ] == ["amount"]
    # The test clock is frozen, so the creation and the change share a timestamp: find the
    # change by its content rather than by position.
    entry = next(
        e
        for e in AuditService(app.state.engine).list(
            roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
        )
        if (e.new or {}).get("formula_sql") == "SELECT SUM(amount) FROM fact_loan"
    )
    assert entry.via == "ai"
    assert entry.new == {
        "formula_sql": "SELECT SUM(amount) FROM fact_loan",
        "links": [model["amount"]["id"]],
    }


def test_a_proposal_the_dw_schema_cannot_satisfy_is_refused_at_once(roles: RoleClients, model):
    kpi = created(roles)

    with pytest.raises(ApiError) as bad_sql:
        propose(roles, kpi, formula_sql="SELECT SUM(nope) FROM fact_loan")
    with pytest.raises(ApiError) as bad_link:
        propose(roles, kpi, links=[str(uuid.uuid4())])
    with pytest.raises(ApiError) as bad_field:
        propose(roles, kpi, name="Renamed")

    for refused in (bad_sql, bad_link, bad_field):
        assert refused.value.status_code == 422


def test_a_kpi_changed_since_the_proposal_makes_the_item_stale(roles: RoleClients, model):
    kpi = created(roles)
    proposed = propose(roles, kpi, formula_sql="SELECT SUM(amount) FROM fact_loan")
    patch_formula(roles, kpi, "SELECT SUM(rate) FROM fact_loan")

    result = accept(roles, proposed.change_set.id)

    assert [s["reason"] for s in result["skipped"]] == ["stale"]
    unchanged = roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}").json()
    assert unchanged["formula_sql"] == "SELECT SUM(rate) FROM fact_loan"
