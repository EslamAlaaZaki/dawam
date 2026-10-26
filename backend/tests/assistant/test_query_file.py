"""The assistant's ``query_file`` tool (#398): one read-only DuckDB SELECT over an uploaded CSV
or XLSX, as the member asking. The model is the scripted fake provider, so the tests see what
the tool put in front of it. The sandbox itself is covered in ``test_file_query_engine``."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from tests.assistant import test_chat
from tests.assistant.test_chat import add_system, tool_names
from tests.assistant.test_run_source_query import app_database_text
from tests.assistant.test_source_tools import level, payload, use

model = test_chat.model
settings = test_chat.settings

IQAMA = "2000000006"
EMAIL = "zz-sentinel@leak.example"
SALES = (
    "region,amount,customer_ref,contact\n"
    f"north,10,{IQAMA},{EMAIL}\n"
    "south,20,2123456788,n/a\n"
    "north,5,2000000006,n/a\n"
).encode()


@pytest.fixture
def sales(roles) -> str:
    """A CSV in a Source System's file area; the file's id."""
    system = add_system(roles)
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems/{system}/files",
        files={"file": ("sales.csv", SALES, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_query_file_answers_a_group_by_over_an_uploaded_csv(roles, model, fake_llm, sales):
    level(roles, "documents")
    sql = "SELECT region, SUM(amount) AS total FROM data GROUP BY region ORDER BY region"

    status, seen = use(roles, fake_llm, "query_file", file_id=sales, sql=sql)

    assert status == "ok"
    result = payload(seen)
    assert result["name"] == "sales.csv"
    assert result["columns"] == ["region", "total"]
    assert result["rows"] == [["north", 15], ["south", 20]]
    assert {c["name"] for c in result["table"]} == {"region", "amount", "customer_ref", "contact"}


def test_personal_data_in_the_file_never_reaches_the_model(roles, model, fake_llm, sales):
    level(roles, "documents")

    status, seen = use(roles, fake_llm, "query_file", file_id=sales, sql="SELECT * FROM data")

    assert status == "ok"
    result = payload(seen)
    assert set(result["masked_columns"]) == {"customer_ref", "contact"}
    assert IQAMA not in seen and EMAIL not in seen
    assert result["rows"][0] == ["north", 10, "[masked]", "[masked]"]


def test_writes_and_file_access_are_rejected(roles, model, fake_llm, sales):
    level(roles, "documents")

    for sql in ("DELETE FROM data", "COPY data TO 'leak.csv'"):
        status, seen = use(roles, fake_llm, "query_file", file_id=sales, sql=sql)
        assert status == "error" and "query_rejected" in seen

    status, seen = use(
        roles, fake_llm, "query_file", file_id=sales, sql="SELECT * FROM read_text('/etc/passwd')"
    )
    assert status == "error" and "query_failed" in seen and "root" not in seen


def test_results_are_capped_at_1000_rows(roles, model, fake_llm, sales):
    level(roles, "documents")

    status, seen = use(
        roles, fake_llm, "query_file", file_id=sales, sql="SELECT * FROM range(5000) t(n)"
    )

    assert status == "ok"
    result = payload(seen)
    assert result["truncated"] is True
    assert "1000 rows" in result["note"]


def test_query_file_needs_the_documents_data_sharing_level(roles, model, fake_llm, sales):
    status, seen = use(roles, fake_llm, "query_file", file_id=sales, sql="SELECT * FROM data")

    assert status == "refused" and "documents" in seen
    assert "query_file" not in tool_names(fake_llm)
    assert "north" not in seen


def test_a_file_of_another_workspace_is_not_found(roles, model, fake_llm):
    level(roles, "documents")

    status, seen = use(
        roles,
        fake_llm,
        "query_file",
        file_id="00000000-0000-0000-0000-000000000001",
        sql="SELECT 1",
    )

    assert status == "error" and "not_found" in seen


def test_no_row_value_is_kept_on_the_run(roles, model, fake_llm, sales, app: FastAPI):
    level(roles, "documents")
    sql = "SELECT 'computed-' || region AS tag FROM data"

    status, seen = use(roles, fake_llm, "query_file", file_id=sales, sql=sql)

    assert status == "ok" and "computed-north" in seen
    assert "computed-north" not in app_database_text(app)
