"""The DW score, end to end (spec §6.11, stories 114-118, 121): recalculation after design
changes, the stored trend and latest results, and the assistant's ``get_score`` tool."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from dawam.modules.warehouse import ScoreScheduler, ScoreService
from dawam.modules.workspaces import WorkspaceService
from tests.assistant import test_chat
from tests.assistant.test_source_tools import payload, use
from tests.roles import RoleClients
from tests.warehouse.test_column_mappings import base, column, mapping_path, table
from tests.warehouse.test_mapping_branches import add_branch, put_branch_column

model = test_chat.model
"""The assistant tests' fixture: an assigned agent model."""

GRAIN = "One row per order line"


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def score(roles: RoleClients, role="viewer") -> dict:
    response = roles.client(role).get(f"{base(roles)}/score")
    assert response.status_code == 200, response.text
    return response.json()


def history(roles: RoleClients) -> list[dict]:
    response = roles.client("viewer").get(f"{base(roles)}/score/history")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def failed(result: dict, code: str) -> list[dict]:
    return [f for f in result["failed_checks"] if f["check_code"] == code]


def names(checks: list[dict]) -> list[str]:
    return [c["object_name"] for c in checks]


def layer(result: dict, name: str) -> dict:
    return next(item for item in result["layers"] if item["layer"] == name)


def fact(roles: RoleClients, name="fact_sales", layer="core", **body) -> dict:
    payload = {"layer": layer, "name": name, "kind": "fact", "grain": GRAIN}
    response = roles.client("editor").post(
        f"{base(roles)}/tables", json=payload | {"fact_type": "transactional"} | body
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_score_needs_a_data_warehouse(roles: RoleClients):
    response = roles.client("viewer").get(f"{base(roles)}/score")
    assert response.status_code == 404
    assert roles.client("viewer").get(f"{base(roles)}/score/history").status_code == 404


def test_an_empty_model_is_not_scored(warehouse: RoleClients):
    result = score(warehouse)

    assert result["score"] is None and result["grade"] is None
    assert [(item["layer"], item["scored"]) for item in result["layers"]] == [
        ("staging", False),
        ("core", False),
        ("mart", False),
    ]
    assert result["failed_checks"] == []


def test_failed_checks_carry_severity_a_link_and_a_fix_hint(warehouse: RoleClients):
    sales = fact(warehouse)

    result = score(warehouse)

    [no_dimension] = failed(result, "fact_has_dimension_fk")
    assert no_dimension["severity"] == "error"
    assert no_dimension["object_name"] == "fact_sales"
    assert no_dimension["object_type"] == "table"
    assert no_dimension["link"] == f"?table={sales['id']}"
    assert no_dimension["fix_hint"]
    assert result["grade"] in ("C", "D", "F")  # an error caps the grade at C
    assert [t["name"] for t in result["tables"]] == ["fact_sales"]
    assert layer(result, "core")["scored"] and not layer(result, "mart")["scored"]


def test_a_column_check_links_to_the_column(warehouse: RoleClients):
    sales = fact(warehouse)
    amount = column(warehouse, sales["id"], "amount", "decimal", role="measure")

    [missing] = failed(score(warehouse), "measure_has_additivity")

    assert missing["object_type"] == "column" and missing["object_id"] == amount["id"]
    assert missing["link"] == f"?table={sales['id']}&column={amount['id']}"


def test_the_score_follows_every_design_change(warehouse: RoleClients):
    customers = table(warehouse, "dim_customer", "core")
    sales = fact(warehouse)
    before = score(warehouse)
    assert failed(before, "fact_has_dimension_fk")

    # Adding a foreign key to the dimension fixes the error without anyone asking for a score.
    key = warehouse.client("editor").post(
        f"{base(warehouse)}/tables/{sales['id']}/columns",
        json={
            "name": "customer_key",
            "data_type": {"type": "bigint"},
            "role": "fk",
            "references_table_id": customers["id"],
        },
    )
    assert key.status_code == 201, key.text
    after = score(warehouse)
    assert not failed(after, "fact_has_dimension_fk")
    assert after["score"] > before["score"]

    # Editing the grain away, deleting a column and the table itself are design changes too.
    patched = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{sales['id']}",
        json={"version": 1, "grain": "x"},
    )
    assert patched.status_code == 200, patched.text
    assert len(history(warehouse)) >= 3
    deleted = warehouse.client("editor").delete(f"{base(warehouse)}/tables/{sales['id']}")
    assert deleted.status_code == 204, deleted.text
    assert [t["name"] for t in score(warehouse)["tables"]] == ["dim_customer"]


def test_a_manual_mapping_edit_recalculates(warehouse: RoleClients):
    core = table(warehouse, "crm_customer", "core")
    column(warehouse, core["id"], "full_name")
    mart = table(warehouse, "dim_customer", "mart")
    name = column(warehouse, mart["id"], "name")
    runs = len(history(warehouse))
    assert "dim_customer.name" in names(failed(score(warehouse), "column_covered"))

    saved = warehouse.client("editor").put(
        f"{mapping_path(warehouse, mart['id'])}/columns/{name['id']}",
        json={"mapping_type": "direct", "sql_expression": "crm_customer.full_name"},
    )

    assert saved.status_code == 200, saved.text
    assert "dim_customer.name" not in names(failed(score(warehouse), "column_covered"))
    assert len(history(warehouse)) > runs


def test_branch_coverage_and_deleting_a_branch_recalculate(warehouse: RoleClients):
    crm = table(warehouse, "crm_customer", "core")
    column(warehouse, crm["id"], "full_name")
    cb = table(warehouse, "cb_customer", "core")
    column(warehouse, cb["id"], "full_name")
    mart = table(warehouse, "dim_customer", "mart")
    name = column(warehouse, mart["id"], "name")
    model = {"mart": mart, "name": name}
    first = add_branch(warehouse, model).json()["branches"][0]["id"]
    second = add_branch(warehouse, model, name="CB", driving_input="cb_customer").json()[
        "branches"
    ][1]["id"]
    put_branch_column(
        warehouse,
        model,
        first,
        "name",
        mapping_type="direct",
        sql_expression="crm_customer.full_name",
    )

    [gap] = [
        f
        for f in failed(score(warehouse), "column_covered")
        if f["object_name"] == "dim_customer.name"
    ]
    assert "CB" in gap["message"]  # not mapped in the second branch

    deleted = warehouse.client("editor").delete(
        f"{mapping_path(warehouse, mart['id'])}/branches/{second}"
    )
    assert deleted.status_code in (200, 204), deleted.text
    assert "dim_customer.name" not in names(failed(score(warehouse), "column_covered"))


def test_the_naming_rules_are_scored(warehouse: RoleClients):
    table(warehouse, "customer", "core")

    [bad] = failed(score(warehouse), "naming_conventions")

    assert bad["severity"] == "warning" and "dim_" in bad["message"]
    changed = warehouse.client("editor").get(base(warehouse)).json()
    patch = warehouse.client("editor").patch(
        base(warehouse),
        json={"version": changed["version"], "naming_rules": {"dimension_prefix": ""}},
    )
    assert patch.status_code == 200, patch.text
    assert not failed(score(warehouse), "naming_conventions")


def test_each_run_adds_a_summary_but_only_the_latest_results_are_kept(warehouse: RoleClients, app):
    table(warehouse, "dim_customer", "core")
    table(warehouse, "dim_account", "core")
    table(warehouse, "dim_branch", "core")

    runs = history(warehouse)

    assert len(runs) >= 3
    stamps = [datetime.fromisoformat(r["created_at"]) for r in runs]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    assert runs[-1]["score"] is not None and runs[-1]["grade"] in "ABCDF"
    assert {item["layer"] for item in runs[-1]["layers"]} == {"staging", "core", "mart"}
    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("select count(*) from score_check_results")).scalar_one()
        distinct = conn.execute(
            sa.text(
                "select count(*) from (select distinct check_code, object_id from "
                "score_check_results) s"
            )
        ).scalar_one()
    assert rows == distinct  # one result per check and object, whatever the number of runs
    assert len(failed(score(warehouse), "dimension_has_keys")) == 3  # no natural key yet


def test_history_is_limited_to_the_latest_runs_oldest_first(warehouse: RoleClients):
    for name in ("dim_a", "dim_b", "dim_c"):
        table(warehouse, name, "core")

    everything = history(warehouse)
    latest = warehouse.client("viewer").get(f"{base(warehouse)}/score/history?limit=2").json()

    assert latest["items"] == everything[-2:]


def test_a_failing_recalculation_never_fails_the_change(warehouse: RoleClients, app, monkeypatch):
    def boom(self, data_warehouse_id, **kw):
        raise RuntimeError("scoring broke")

    monkeypatch.setattr(ScoreService, "recalculate", boom)

    response = warehouse.client("editor").post(
        f"{base(warehouse)}/tables", json={"layer": "core", "name": "dim_a", "kind": "dimension"}
    )

    assert response.status_code == 201, response.text


def test_two_hundred_tables_are_scored_within_two_seconds(warehouse: RoleClients, app):
    engine = app.state.engine
    now = datetime.now(UTC)
    with engine.begin() as conn:
        dw = conn.execute(sa.text("select id from data_warehouses")).scalar_one()
        dims = [uuid.uuid4() for _ in range(100)]
        for i in range(200):
            is_dim = i < 100
            table_id = dims[i] if is_dim else uuid.uuid4()
            conn.execute(
                sa.text(
                    "insert into dw_tables (id, data_warehouse_id, layer, name, kind, fact_type,"
                    " grain, is_aggregate, scd_type, is_conformed, description, created_at,"
                    " updated_at, version) values (:id, :dw, :layer, :name, :kind, :ft, :grain,"
                    " false, :scd, false, '', :now, :now, 1)"
                ),
                {
                    "id": table_id,
                    "dw": dw,
                    "layer": "core" if i % 2 else "mart",
                    "name": f"{'dim' if is_dim else 'fact'}_t{i}",
                    "kind": "dimension" if is_dim else "fact",
                    "ft": None if is_dim else "transactional",
                    "grain": None if is_dim else GRAIN,
                    "scd": 1 if is_dim else None,
                    "now": now,
                },
            )
            for ordinal, (name, role, ref) in enumerate(
                [("t_key", "sk", None), ("t_code", "nk", None), ("fk_a", "fk", dims[i % 100])]
                + [(f"m{j}", "measure", None) for j in range(17)]
            ):
                conn.execute(
                    sa.text(
                        "insert into dw_columns (id, table_id, name, ordinal, data_type,"
                        " is_nullable, role, references_table_id, description, is_system,"
                        " created_at, updated_at, version) values (:id, :t, :name, :o,"
                        " cast('{\"type\": \"integer\"}' as json), true, :role, :ref, '', false,"
                        " :now, :now, 1)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "t": table_id,
                        "name": name,
                        "o": ordinal,
                        "role": role,
                        "ref": ref,
                        "now": now,
                    },
                )
    service = ScoreService(
        engine,
        workspaces=WorkspaceService(engine, clock=app.state.services.clock),
        clock=app.state.services.clock,
    )

    started = time.perf_counter()
    report = service.recalculate(dw)
    elapsed = time.perf_counter() - started

    assert report is not None and len(report.tables) == 200
    assert elapsed < 2, f"scoring 200 tables took {elapsed:.2f}s"


# --- debouncing ----------------------------------------------------------------------


def test_a_burst_of_changes_is_scored_once_after_the_quiet_moment():
    runs: list[uuid.UUID] = []
    done = threading.Event()

    def recalculate(warehouse_id: uuid.UUID) -> None:
        runs.append(warehouse_id)
        done.set()

    scheduler = ScoreScheduler(recalculate, debounce_seconds=0.05)
    one, other = uuid.uuid4(), uuid.uuid4()
    for _ in range(5):
        scheduler.request(one)
    scheduler.request(other)
    assert runs == []  # nothing is scored while the burst goes on
    time.sleep(0.4)
    assert sorted(runs) == sorted([one, other])
    scheduler.request(one)  # a later change starts a new window
    time.sleep(0.3)
    assert runs.count(one) == 2


def test_a_zero_debounce_scores_at_once_and_a_shutdown_drops_pending_runs():
    runs: list[uuid.UUID] = []
    ScoreScheduler(runs.append, debounce_seconds=0).request(uuid.uuid4())
    assert len(runs) == 1
    pending = ScoreScheduler(runs.append, debounce_seconds=0.05)
    pending.request(uuid.uuid4())
    pending.shutdown()
    time.sleep(0.2)
    assert len(runs) == 1


# --- the assistant's tool ------------------------------------------------------------


def test_get_score_tells_the_assistant_the_score_and_what_to_fix(
    warehouse: RoleClients,
    model,
    fake_llm,
):
    fact(warehouse)

    status, seen = use(warehouse, fake_llm, "get_score")

    assert status == "ok"
    result = payload(seen)
    assert result["grade"] in ("C", "D", "F") and result["capped_at_c_by_errors"] in (True, False)
    assert {item["layer"]: item["score"] for item in result["layers"]}["mart"] == "not scored"
    [no_dimension] = [f for f in result["failed_checks"] if f["check"] == "fact_has_dimension_fk"]
    assert no_dimension["severity"] == "error" and no_dimension["fix_hint"] and no_dimension["link"]


def test_get_score_before_the_data_warehouse_is_set_up_is_an_error_not_a_crash(
    roles: RoleClients,
    model,
    fake_llm,
):
    status, _ = use(roles, fake_llm, "get_score")

    assert status == "error"
