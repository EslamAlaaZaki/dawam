"""The assistant's ``run_source_query`` tool (ADR 0002, spec §6.8, story 68).

The source is a scratch PostgreSQL database; ``ref`` is a column with a vague name that holds
Iqama numbers. The model is the scripted fake provider, so the tests see what the tool put in
front of it, and the app database is read directly to prove no value is stored. Cases that
need a hostile database (an error that echoes a value, odd types) swap the Connector.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from dawam.modules.llm import FakeAdapter, Reply
from dawam.modules.sources import source_query_service
from dawam.modules.sources.internal.connector import ConnectorError, QueryResult
from dawam.platform.config import Settings
from tests.assistant import test_chat
from tests.assistant.test_chat import ask, base, conversation, tool, tool_names
from tests.assistant.test_source_tools import level, payload, system_id
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_pii_findings import findings

scratch_source = test_extraction.scratch_source
model = test_chat.model

IQAMAS = ["2000000006", "2123456788", "2000000006"]
NAMES = ["Amira Hassan", "Omar Saleh", "Layla Nasser"]
EMAIL = "zz-sentinel@leak.example"
SECRETS = [*IQAMAS, EMAIL]
BUDGET_SECONDS = 1


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "assistant_max_tool_calls": 10,
            "assistant_source_query_seconds": BUDGET_SECONDS,
        }
    )


@pytest.fixture
def source(roles: RoleClients, scratch_source) -> str:
    """An extracted system with ``people`` (vague column names, one holding Iqamas) and a
    ``big`` table; the Workspace shares sample rows."""
    scratch_source["run"](
        "CREATE TABLE people (id integer PRIMARY KEY, ref text, who text, contact text);"
        "CREATE TABLE big (id integer PRIMARY KEY, grp integer);"
    )
    for i in range(3):
        scratch_source["run"](
            f"INSERT INTO people VALUES ({i + 1}, '{IQAMAS[i]}', '{NAMES[i]}', "
            f"'{EMAIL if i == 0 else 'n/a'}');"
        )
    scratch_source["run"]("INSERT INTO big SELECT g, g % 7 FROM generate_series(1, 150) g;")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    level(roles, "samples")
    return system


def run_queries(
    roles: RoleClients,
    fake_llm: FakeAdapter,
    system: str,
    *sql: str,
    as_role: str = "editor",
) -> list[tuple[str, str]]:
    """Have the model run each ``sql`` in turn; return (status, what the model then saw)."""
    fake_llm.calls.clear()
    calls = [
        Reply(tool_calls=(tool("run_source_query", f"c{i}", system_id=system_id(system), sql=q),))
        for i, q in enumerate(sql)
    ]
    fake_llm.script(*calls, Reply(text="done"))
    client = roles.client(as_role)
    events = ask(client, roles, conversation(client, roles), "Look at the data")
    statuses = [shown["status"] for kind, shown in events if kind == "tool"]
    seen = [fake_llm.calls[i + 1].messages[-1].content or "" for i in range(len(sql))]
    return list(zip(statuses, seen, strict=True))


def app_database_text(app: FastAPI) -> str:
    rows: list[str] = []
    with app.state.engine.connect() as conn:
        for table in sa.inspect(conn).get_table_names():
            result = conn.execute(sa.text(f'SELECT row_to_json(t)::text FROM "{table}" t'))
            rows.extend(row[0] for row in result)
    return "\n".join(rows)


class FakeConnector:
    """Stands in for the source: records the SQL and answers (or fails) as scripted."""

    def __init__(self, result: QueryResult | Exception) -> None:
        self.result = result
        self.sent: list[str] = []

    def factory(self, engine: str, params: Any) -> FakeConnector:
        self.engine, self.params = engine, params
        return self

    def query(self, sql: str, *, limit: int = 100) -> QueryResult:
        self.sent.append(sql)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fake_source(monkeypatch: pytest.MonkeyPatch):
    def install(result: QueryResult | Exception) -> FakeConnector:
        connector = FakeConnector(result)
        monkeypatch.setattr(source_query_service, "connector_for", connector.factory)
        return connector

    return install


# -- what the model gets back ---------------------------------------------------------------


def test_a_count_query_runs_and_the_result_is_shown_to_the_model(roles, model, fake_llm, source):
    [(status, seen)] = run_queries(
        roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people"
    )

    assert status == "ok"
    result = payload(seen)
    assert result["columns"] == ["n"] and result["rows"] == [[3]]
    assert result["row_count"] == 1 and result["duration_ms"] >= 0
    assert "masked_columns" not in result


def test_a_join_on_a_column_holding_iqamas_can_be_counted(roles, model, fake_llm, source):
    [(status, seen)] = run_queries(
        roles,
        fake_llm,
        source,
        "SELECT COUNT(*) AS n FROM public.people a JOIN public.people b ON a.ref = b.ref",
    )

    assert status == "ok" and payload(seen)["rows"] == [[5]]  # 3 rows, two share an Iqama


def test_a_vague_named_column_of_iqamas_is_masked_and_becomes_a_finding(
    roles, model, fake_llm, source
):
    assert [f for f in findings(roles, source) if f["column"] == "ref"] == []

    [(status, seen)] = run_queries(
        roles, fake_llm, source, "SELECT id, ref, who FROM public.people ORDER BY id"
    )

    assert status == "ok"
    result = payload(seen)
    assert result["columns"] == ["id", "ref", "who"]
    assert result["masked_columns"] == ["ref"]
    assert [row[1] for row in result["rows"]] == ["[masked]"] * 3
    assert [row[2] for row in result["rows"]] == NAMES  # a name is not a validator's business
    assert not any(value in seen for value in IQAMAS)
    [finding] = [f for f in findings(roles, source) if f["column"] == "ref"]
    assert (finding["rule"], finding["status"], finding["is_protected"]) == (
        "value-at-query",
        "suggested",
        True,
    )
    assert finding["category"] == "direct_identifier"
    assert not any(value in json.dumps(finding) for value in IQAMAS)


def test_the_next_query_masks_the_column_by_name_and_refuses_to_project_it(
    roles, model, fake_llm, source
):
    run_queries(roles, fake_llm, source, "SELECT ref FROM public.people")

    [(status, seen)] = run_queries(roles, fake_llm, source, "SELECT ref FROM public.people")

    assert status == "error" and "query_rejected" in seen and "ref" in seen
    assert not any(value in seen for value in IQAMAS)


def test_an_email_behind_a_vague_name_is_masked_too(roles, model, fake_llm, source):
    [(_, seen)] = run_queries(roles, fake_llm, source, "SELECT contact FROM public.people")

    assert payload(seen)["masked_columns"] == ["contact"]
    assert EMAIL not in seen


def test_a_row_cap_of_one_hundred_is_enforced(roles, model, fake_llm, source):
    [(status, seen)] = run_queries(
        roles, fake_llm, source, "SELECT id, grp FROM public.big ORDER BY id"
    )

    result = payload(seen)
    assert status == "ok" and result["row_count"] == 100 and result["truncated"] is True
    assert len(result["rows"]) == 100


# -- the guard is the only way in -----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM public.people",
        "SELECT 1; DROP TABLE public.people",
        "SELECT pg_sleep(30)",
        "SELECT * FROM public.people FOR UPDATE",
        "SELECT * INTO public.copy FROM public.people",
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT * FROM otherdb.public.people",
        "WITH x AS (DELETE FROM public.people RETURNING *) SELECT * FROM x",
        "SELECT dblink_exec('x', 'y')",
        "SELECT lo_import('/etc/passwd')",
        "COPY public.people TO PROGRAM 'id'",
    ],
)
def test_sql_the_guard_rejects_never_reaches_the_source(
    roles, model, fake_llm, source, fake_source, sql
):
    connector = fake_source(QueryResult(("x",), ((1,),), False))

    [(status, seen)] = run_queries(roles, fake_llm, source, sql)

    assert status == "error" and "query_rejected" in seen
    assert connector.sent == []


def test_only_the_safe_sql_the_guard_regenerates_is_sent_never_the_models_text(
    roles, model, fake_llm, source, fake_source
):
    connector = fake_source(QueryResult(("n",), ((3,),), False))
    written = "select   count(*)   as n from  people /* sneaky */"

    run_queries(roles, fake_llm, source, written.replace("people", "public.people"))

    [sent] = connector.sent
    assert "sneaky" not in sent and sent != written
    assert '"public"."people"' in sent  # every table fully qualified


def test_the_connection_gets_the_timeout_row_limit_and_read_only_session(
    roles, model, fake_llm, source, fake_source
):
    connector = fake_source(QueryResult(("n",), ((3,),), False))

    run_queries(roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people")

    assert connector.params.options["statement_timeout_seconds"] == BUDGET_SECONDS


def test_a_query_cannot_write_even_through_a_user_that_could(roles, model, fake_llm, source):
    """The scratch database user is a superuser: only the guard and the read-only session
    stand between the model and a write."""
    run_queries(roles, fake_llm, source, "DELETE FROM public.people")
    run_queries(roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people")
    [(_, seen)] = run_queries(roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people")

    assert payload(seen)["rows"] == [[3]]


# -- the budget and the timeout -------------------------------------------------------------

HEAVY = "SELECT COUNT(*) AS n FROM public.big a " + " ".join(
    f"JOIN public.big {name} ON 1 = 1" for name in "bcdef"
)


def test_the_statement_timeout_stops_a_slow_query_and_the_budget_is_then_used_up(
    roles, model, fake_llm, source
):
    slow, after = run_queries(
        roles, fake_llm, source, HEAVY, "SELECT COUNT(*) AS n FROM public.people"
    )

    assert slow[0] == "error" and "statement timeout" in slow[1]
    assert after[0] == "error" and "budget_exhausted" in after[1]


def test_each_run_has_a_budget_of_its_own(roles, model, fake_llm, source):
    run_queries(roles, fake_llm, source, HEAVY)

    [(status, _)] = run_queries(roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people")

    assert status == "ok"


# -- values that reach the model, or storage ------------------------------------------------


def test_numbers_bytes_and_text_around_a_value_are_all_checked(
    roles, model, fake_llm, source, fake_source
):
    fake_source(
        QueryResult(
            ("a", "b", "c", "d", "e", "ok"),
            (
                (
                    Decimal("2000000006"),
                    b"2000000006",
                    "call 0501234567 now",
                    "٢" + "0" * 8 + "6",
                    2000000006.0,
                    "fine",
                ),
                (Decimal("1"), b"x", "nothing", "x", 1.5, "fine"),
            ),
            False,
        )
    )

    [(_, seen)] = run_queries(
        roles,
        fake_llm,
        source,
        "SELECT id AS a, ref AS b, who AS c, contact AS d, id AS e, who AS ok FROM public.people",
    )

    result = payload(seen)
    assert result["masked_columns"] == ["a", "b", "c", "d", "e"]
    assert "2000000006" not in seen and "0501234567" not in seen
    assert result["rows"][0][-1] == "fine"


def test_a_database_error_that_echoes_a_value_never_reaches_the_model(
    roles, model, fake_llm, source, fake_source
):
    fake_source(
        ConnectorError(
            "invalid_query", f'invalid input syntax for integer: "{IQAMAS[0]}" for {EMAIL}'
        )
    )
    [(status, seen)] = run_queries(roles, fake_llm, source, "SELECT id FROM public.people")

    assert status == "error" and "source_error" in seen
    assert not any(value in seen for value in SECRETS)


def test_an_unexpected_driver_failure_is_reported_without_its_text(
    roles, model, fake_llm, source, fake_source
):
    fake_source(RuntimeError(f"duplicate key value violates (national_id)=({IQAMAS[0]})"))

    [(status, seen)] = run_queries(roles, fake_llm, source, "SELECT id FROM public.people")

    assert status == "error" and not any(value in seen for value in SECRETS)


def test_no_value_is_stored_logged_or_audited(
    roles, model, fake_llm, source, app: FastAPI, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG):
        run_queries(
            roles,
            fake_llm,
            source,
            "SELECT id, ref, who, contact FROM public.people",
            "SELECT COUNT(*) AS n FROM public.people WHERE who <> 'Nobody Here'",
        )

    stored = app_database_text(app) + caplog.text
    for value in [*SECRETS, *NAMES]:
        assert value not in stored, value
    assert "Nobody Here" in stored  # the model's own SQL is kept (redacted of PII only)


def test_saved_tool_results_keep_only_query_columns_row_count_and_duration(
    roles, model, fake_llm, source, app: FastAPI
):
    with app.state.engine.begin() as conn:  # the last connection test found write privileges
        conn.execute(sa.text("UPDATE connections SET can_write = true"))
    client = roles.client("editor")
    cid = conversation(client, roles)
    fake_llm.script(
        Reply(
            tool_calls=(
                tool(
                    "run_source_query",
                    system_id=system_id(source),
                    sql="SELECT id, who FROM public.people ORDER BY id",
                ),
            )
        ),
        Reply(text="done"),
    )
    events = ask(client, roles, cid, "Look")

    [(_, shown)] = [e for e in events if e[0] == "tool"]
    saved = client.get(f"{base(roles)}/conversations/{cid}").json()
    [run] = [m["run"] for m in saved["messages"] if m["run"]]
    [call] = run["tool_calls"]
    for record in (shown, call):
        assert set(record["result"]) == {"columns", "row_count", "duration_ms", "can_write"}
        assert record["result"]["columns"] == ["id", "who"] and record["result"]["row_count"] == 3
        assert record["result"]["can_write"] is True  # the UI warns strongly
        assert call["arguments"]["sql"] == "SELECT id, who FROM public.people ORDER BY id"
    assert not any(name in json.dumps(saved) for name in NAMES)


def test_model_written_text_is_redacted_before_it_is_saved(roles, model, fake_llm, source):
    iban = "SA0380000000608010167519"
    client = roles.client("editor")
    cid = conversation(client, roles)
    fake_llm.script(
        Reply(
            tool_calls=(
                tool(
                    "run_source_query",
                    system_id=system_id(source),
                    sql="SELECT COUNT(*) AS n FROM public.people WHERE who <> '2000000006'",
                ),
            )
        ),
        Reply(text=f"The account {iban} and Iqama 2000000006 and {EMAIL} appear to match."),
    )

    ask(client, roles, cid, "Look")

    saved = client.get(f"{base(roles)}/conversations/{cid}").json()
    [answer] = [m for m in saved["messages"] if m["role"] == "assistant"]
    assert "[redacted: iban]" in answer["content"] and "[redacted: email]" in answer["content"]
    [run] = [m["run"] for m in saved["messages"] if m["run"]]
    [call] = run["tool_calls"]
    assert "[redacted: " in call["arguments"]["sql"]
    text = json.dumps(saved)
    assert iban not in text and "2000000006" not in text and EMAIL not in text


# -- who, when and where --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "allowed"), [("viewer", False), ("editor", True), ("owner", True)]
)
def test_only_owners_and_editors_may_run_source_queries(
    roles, model, fake_llm, source, role, allowed
):
    [(status, seen)] = run_queries(
        roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people", as_role=role
    )

    assert ("run_source_query" in tool_names(fake_llm)) is allowed
    assert status == ("ok" if allowed else "refused")
    assert ("Refused" in seen) is not allowed


@pytest.mark.parametrize("value", ["metadata", "profiles", "documents"])
def test_the_tool_needs_the_samples_level(roles, model, fake_llm, source, value):
    level(roles, value)

    [(status, seen)] = run_queries(
        roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people", as_role="owner"
    )

    assert "run_source_query" not in tool_names(fake_llm)
    assert status == "refused" and "samples" in seen


def test_a_system_without_a_live_connection_cannot_be_queried(roles, model, fake_llm, source):
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "Bare", "code": "bare"}
    )
    bare = f"/api/v1/workspaces/{roles.workspace_id}/systems/{response.json()['id']}"

    [(status, seen)] = run_queries(
        roles, fake_llm, bare, "SELECT COUNT(*) AS n FROM public.people", as_role="owner"
    )

    assert status == "error" and "connection_missing" in seen


def test_the_name_rules_run_first_if_they_have_not_run_on_the_latest_snapshot(
    roles, model, fake_llm, scratch_source, app: FastAPI
):
    scratch_source["run"]("CREATE TABLE c (id integer PRIMARY KEY, national_id text);")
    scratch_source["run"]("INSERT INTO c VALUES (1, 'x');")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    level(roles, "samples")
    with app.state.engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM pii_findings"))
    assert findings(roles, system) == []

    [(_, seen)] = run_queries(roles, fake_llm, system, "SELECT national_id FROM public.c")

    assert "query_rejected" in seen  # protected before the first exploration, so not projected
    assert [f["column"] for f in findings(roles, system, status="suggested")] == ["national_id"]


def test_every_run_is_audited_without_result_values(roles, model, fake_llm, source):
    run_queries(
        roles,
        fake_llm,
        source,
        "SELECT id, ref FROM public.people",
        "DELETE FROM public.people",
    )

    audit = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/audit")
    entries = [e for e in audit.json()["items"] if e["entity_type"] == "source_query"]
    assert len(entries) == 2
    [ran] = [e for e in entries if e["new"]["outcome"] == "ok"]
    [rejected] = [e for e in entries if e["new"]["outcome"] == "rejected"]
    assert {e["via"] for e in entries} == {"ai"}
    assert ran["new"]["outcome"] == "ok" and ran["new"]["row_count"] == 3
    assert ran["new"]["columns"] == ["id", "ref"] and ran["new"]["masked_columns"] == ["ref"]
    assert rejected["new"]["outcome"] == "rejected" and rejected["new"]["error_code"]
    assert not any(value in json.dumps(entries) for value in SECRETS)
    assert ran["actor"] == rejected["actor"] and ran["actor"] is not None


# -- engine limits, weak rules, the wire ---------------------------------------------------


@pytest.mark.parametrize(("can_write", "runs"), [(None, False), (True, False), (False, True)])
def test_sql_server_is_queried_only_when_the_login_is_proven_read_only(
    roles, model, fake_llm, source, fake_source, app: FastAPI, can_write, runs
):
    connector = fake_source(QueryResult(("n",), ((3,),), False))
    with app.state.engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE connections SET engine = 'sqlserver', can_write = :w"),
            {"w": can_write},
        )

    [(status, seen)] = run_queries(
        roles, fake_llm, source, "SELECT COUNT(*) AS n FROM public.people"
    )

    assert (status == "ok") is runs and (len(connector.sent) == 1) is runs
    if not runs:
        assert "read_only_login_required" in seen and "read-only login" in seen


def test_the_database_is_asked_for_at_most_one_row_over_the_cap(
    roles, model, fake_llm, source, fake_source
):
    connector = fake_source(QueryResult(("id",), ((1,),), False))

    run_queries(roles, fake_llm, source, "SELECT id FROM public.big")

    assert connector.sent[0].upper().rstrip().endswith("LIMIT 101")


def test_a_column_that_is_mostly_dates_or_registration_numbers_is_masked(
    roles, model, fake_llm, source, fake_source
):
    fake_source(
        QueryResult(
            ("born", "cr", "mixed", "few"),
            (
                ("1990-01-01", "1010123456", "1990-01-01", "1990-01-01"),
                ("1985-06-07", "4030987654", "1985-06-07", None),
                ("2001-12-31", "2050111222", "2001-12-31", None),
                ("1999-02-02", "7000000001", "text", None),
                ("1970-03-03", "1010123457", "more text", None),
            ),
            False,
        )
    )

    [(_, seen)] = run_queries(roles, fake_llm, source, "SELECT who AS born FROM public.people")

    result = payload(seen)
    assert result["masked_columns"] == ["born", "cr"]
    assert [row[0] for row in result["rows"]] == ["[masked]"] * 5
    assert [row[2] for row in result["rows"]][:2] == ["1990-01-01", "1985-06-07"]


def test_the_streamed_tool_frame_is_redacted_like_the_saved_one(roles, model, fake_llm, source):
    client = roles.client("editor")
    fake_llm.script(
        Reply(
            tool_calls=(
                tool(
                    "run_source_query",
                    system_id=system_id(source),
                    sql="SELECT COUNT(*) AS n FROM public.people WHERE who <> '2000000006'",
                ),
            )
        ),
        Reply(text="done"),
    )

    events = ask(client, roles, conversation(client, roles), "Look")

    [(_, shown)] = [e for e in events if e[0] == "tool"]
    assert "2000000006" not in json.dumps(shown) and "[redacted: " in shown["arguments"]["sql"]
