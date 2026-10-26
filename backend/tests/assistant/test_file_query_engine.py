"""The ``query_file`` engine (#398): one read-only SELECT over an uploaded CSV or XLSX in an
in-process DuckDB sandbox. No database, no model: the engine alone, on bytes in memory."""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pytest

from dawam.modules.assistant.internal import file_query
from dawam.modules.assistant.internal.file_query import CSV_MIME, XLSX_MIME, run_file_query
from dawam.platform.errors import ApiError

IQAMAS = ["2000000006", "2123456788", "2000000006"]
EMAIL = "zz-sentinel@leak.example"
PEOPLE = (
    "id,ref,national_id,note,amount\n"
    f"1,{IQAMAS[0]},a1,call {EMAIL},10.5\n"
    f"2,{IQAMAS[1]},a2,fine,20\n"
    f"3,{IQAMAS[2]},a3,ok,30\n"
).encode()


def run(sql: str, data: bytes = PEOPLE, mime: str = CSV_MIME, **kw):
    return run_file_query(data, mime, sql, **kw)


def rejected(sql: str, data: bytes = PEOPLE) -> ApiError:
    with pytest.raises(ApiError) as caught:
        run(sql, data)
    return caught.value


def test_a_select_over_the_csv_returns_typed_rows():
    result = run("SELECT id, amount FROM data WHERE id > 1 ORDER BY id")

    assert result.columns == ("id", "amount")
    assert result.rows == ((2, 20), (3, 30))  # whole numbers shown as such
    assert result.row_count == 2 and not result.truncated
    assert ("amount", "DOUBLE") in [(c.name, c.type) for c in result.table]


def test_an_aggregate_works_like_a_calculator():
    result = run("SELECT SUM(amount) AS total, COUNT(*) AS n FROM data")

    assert result.rows == (("60.5", 3),)


def test_rows_are_capped():
    data = ("n\n" + "\n".join(str(i) for i in range(2000))).encode()

    result = run("SELECT n FROM data", data, row_cap=1000)

    assert result.row_count == 1000 and result.truncated


def test_a_column_with_a_pii_name_or_pii_values_is_masked_before_the_query_runs():
    result = run("SELECT ref, national_id, note FROM data ORDER BY id")

    assert {c.name for c in result.table if c.masked} == {"ref", "national_id", "note"}
    flat = " ".join(str(v) for row in result.rows for v in row)
    assert all(secret not in flat for secret in [*IQAMAS, EMAIL])
    assert result.rows[0] == ("[masked]", "[masked]", "[masked]")


def test_a_masked_column_cannot_be_read_back_piece_by_piece():
    result = run("SELECT substr(ref, 1, 5) AS head, length(ref) AS n FROM data")

    assert "20000" not in str(result.rows)


def test_a_masked_column_can_still_be_counted():
    result = run("SELECT COUNT(ref) AS n FROM data")

    assert result.rows == ((3,),)


def test_pii_built_by_the_query_is_masked_in_the_result():
    result = run("SELECT '2000000006' AS x, id FROM data")

    assert result.masked_columns == ("x",)
    assert all(row[0] == "[masked]" for row in result.rows)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM data",
        "INSERT INTO data (id) VALUES (9)",
        "UPDATE data SET id = 1",
        "CREATE TABLE t AS SELECT 1",
        "DROP TABLE data",
        "SELECT 1; DROP TABLE data",
        "COPY data TO 'out.csv'",
        "ATTACH 'other.db'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET enable_external_access = true",
        "PRAGMA database_list",
    ],
)
def test_anything_but_one_select_is_rejected(sql):
    error = rejected(sql)

    assert error.code == "query_rejected"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('{path}')",
        "SELECT * FROM read_text('{path}')",
        "SELECT * FROM '{path}'",
        "SELECT * FROM glob('*')",
        "SELECT * FROM read_csv('https://example.com/x.csv')",
    ],
)
def test_the_file_system_and_the_network_are_out_of_reach(sql, tmp_path: Path):
    secret = tmp_path / "secret.csv"
    secret.write_text("x\nsentinel-on-disk\n")

    error = rejected(sql.format(path=secret.as_posix()))

    assert error.code == "query_failed"
    assert "sentinel-on-disk" not in error.message


def test_a_long_query_is_interrupted(monkeypatch):
    monkeypatch.setattr(file_query, "TIME_LIMIT_SECONDS", 0.3)

    error = rejected("SELECT COUNT(*) FROM range(100000000000) a, range(1000) b")

    assert error.code == "query_timeout"


def test_an_error_does_not_echo_a_value_from_the_file():
    data = b"code\nX-sentinel-77\n"

    error = rejected("SELECT CAST(code AS INTEGER) FROM data", data)

    assert "sentinel" not in error.message


def test_an_xlsx_sheet_is_queried_by_name():
    book = openpyxl.Workbook()
    book.active.title = "First"
    book.active.append(["a"])
    book.active.append([1])
    sheet = book.create_sheet("Sales")
    sheet.append(["region", "amount"])
    sheet.append(["north", 5])
    sheet.append(["south", 7])
    buffer = io.BytesIO()
    book.save(buffer)

    result = run(
        "SELECT SUM(amount) AS total FROM data", buffer.getvalue(), XLSX_MIME, sheet="Sales"
    )

    assert result.rows == ((12,),)
    with pytest.raises(ApiError) as caught:
        run("SELECT 1", buffer.getvalue(), XLSX_MIME, sheet="Nope")
    assert caught.value.code == "sheet_not_found"


def test_a_file_that_is_not_a_table_is_refused():
    with pytest.raises(ApiError) as caught:
        run("SELECT 1", b"# notes", "text/markdown")

    assert caught.value.code == "not_queryable"
