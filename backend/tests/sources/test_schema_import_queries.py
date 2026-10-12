"""Schema Import helper queries (spec §6.4, stories 47, 48): every engine has a query per
template sheet, and its select list is that sheet's columns, in order.

This is the whole check for Db2 and Teradata, which no container runs; the engines that
have one are also round-tripped (``test_schema_import_roundtrip``).
"""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.schema_import import SHEETS
from dawam.modules.sources.internal.schema_import_queries import (
    ENGINES,
    helper_query,
    helper_script,
    output_columns,
)
from tests.roles import RoleClients
from tests.sources.test_extraction import add_system


def test_every_engine_covers_the_supported_and_the_unconnectable_ones():
    assert {"postgresql", "mysql", "mariadb", "sqlserver", "oracle", "db2", "teradata"} <= set(
        ENGINES
    )


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("sheet", SHEETS, ids=lambda s: s.name)
def test_a_query_returns_exactly_the_columns_of_its_sheet(engine: str, sheet):
    query = helper_query(engine, sheet.name)

    assert output_columns(query) == sheet.column_names
    assert "@scope" not in query


@pytest.mark.parametrize("engine", ENGINES)
def test_a_script_has_every_sheet_and_can_be_limited_to_schemas(engine: str):
    script = helper_script(engine, ("core", "o'brien"))

    for sheet in SHEETS:
        assert f"-- sheet: {sheet.name}\n" in script
    assert "'core', 'o''brien'" in script


def test_the_download_gives_the_script_per_engine(roles: RoleClients):
    system = add_system(roles)

    response = roles.client("editor").get(f"{system}/import/helper-queries?engine=db2")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "db2" in response.headers["content-disposition"]
    assert response.text == helper_script("db2")
    limited = roles.client("editor").get(
        f"{system}/import/helper-queries?engine=postgresql&schemas=core,crm"
    )
    assert "IN ('core', 'crm')" in limited.text
    unknown = roles.client("editor").get(f"{system}/import/helper-queries?engine=sybase")
    assert unknown.status_code == 422
    assert (
        roles.client("viewer").get(f"{system}/import/helper-queries?engine=db2").status_code == 403
    )
