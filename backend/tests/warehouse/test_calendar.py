"""Generated date and time dimensions (spec §6.9, story 90b): the pure calendar rows, the seed
files and the endpoints that add the tables and deliver them in the DDL package."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from dawam.modules.warehouse import DateDimension
from dawam.modules.warehouse.calendar import (
    date_columns,
    date_rows,
    seed_csv,
    time_columns,
    time_rows,
)
from dawam.modules.warehouse.ddl import DdlColumn, DdlTable, SeedData, generate_ddl
from tests.roles import RoleClients
from tests.warehouse.test_core_mart_model import base, column, fact, table


def rows_by_key(settings: DateDimension) -> dict[int, dict]:
    return {r["date_key"]: r for r in date_rows(settings)}


def small(**changes) -> DateDimension:
    return DateDimension(**{"start_year": 2024, "end_year": 2025, **changes})


# --- Gregorian ------------------------------------------------------------------------


def test_one_row_per_day_with_an_integer_yyyymmdd_key():
    rows = date_rows(small())
    assert len(rows) == 366 + 365
    assert rows[0]["date_key"] == 20240101 and rows[0]["full_date"] == date(2024, 1, 1)
    assert rows[-1]["date_key"] == 20251231
    assert isinstance(rows[0]["date_key"], int)


def test_gregorian_attributes():
    row = rows_by_key(small())[20240229]
    assert (row["year"], row["quarter"], row["month"], row["day_of_month"]) == (2024, 1, 2, 29)
    assert row["month_name"] == "February" and row["day_name"] == "Thursday"
    assert row["day_of_week"] == 4 and row["day_of_year"] == 60
    assert row["year_month"] == 202402 and row["is_month_end"] is True


# --- weekend --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weekend,flags",
    [
        # 2024-03-08 Fri, 09 Sat, 10 Sun, 11 Mon
        (("friday", "saturday"), (True, True, False, False)),
        (("saturday", "sunday"), (False, True, True, False)),
        (("friday",), (True, False, False, False)),
    ],
)
def test_weekend_flags_follow_the_setting(weekend, flags):
    rows = rows_by_key(small(weekend_days=weekend))
    got = tuple(rows[k]["is_weekend"] for k in (20240308, 20240309, 20240310, 20240311))
    assert got == flags


# --- Hijri (Umm al-Qura) --------------------------------------------------------------


def test_hijri_attributes_use_umm_al_qura():
    rows = rows_by_key(small(include_hijri=True))
    ramadan = rows[20240311]  # 1 Ramadan 1445 AH
    assert (ramadan["hijri_year"], ramadan["hijri_month"], ramadan["hijri_day"]) == (1445, 9, 1)
    assert ramadan["hijri_month_name"] == "Ramadan"
    assert ramadan["hijri_month_name_ar"] == "رمضان"
    assert ramadan["hijri_year_month"] == 144509
    # Umm al-Qura: 1 Muharram 1446 AH fell on 2024-07-07.
    assert rows[20240707]["hijri_year"] == 1446 and rows[20240707]["hijri_month"] == 1
    assert rows[20240707]["hijri_day"] == 1


def test_hijri_is_null_outside_the_calendar_table():
    rows = date_rows(DateDimension(start_year=1800, end_year=1801, include_hijri=True))
    assert rows[0]["hijri_year"] is None and rows[0]["hijri_month_name_ar"] is None


def test_no_hijri_columns_unless_asked():
    names = [c.name for c in date_columns(small())]
    assert not any(n.startswith("hijri") for n in names)
    with_hijri = [c.name for c in date_columns(small(include_hijri=True))]
    assert {"hijri_year", "hijri_month", "hijri_day"} <= set(with_hijri)


# --- fiscal ---------------------------------------------------------------------------


def test_fiscal_year_starting_in_july_is_named_for_the_year_it_ends():
    rows = rows_by_key(small(fiscal_year_start_month=7))
    june, july = rows[20240630], rows[20240701]
    assert (june["fiscal_year"], june["fiscal_quarter"], june["fiscal_month"]) == (2024, 4, 12)
    assert (july["fiscal_year"], july["fiscal_quarter"], july["fiscal_month"]) == (2025, 1, 1)
    assert rows[20250331]["fiscal_quarter"] == 3 and rows[20250331]["fiscal_month"] == 9


def test_fiscal_year_starting_in_january_equals_the_calendar_year():
    row = rows_by_key(small(fiscal_year_start_month=1))[20240515]
    assert (row["fiscal_year"], row["fiscal_quarter"], row["fiscal_month"]) == (2024, 2, 5)


def test_no_fiscal_columns_without_a_start_month():
    assert not any(c.name.startswith("fiscal") for c in date_columns(small()))
    assert any(c.name == "fiscal_year" for c in date_columns(small(fiscal_year_start_month=4)))


# --- time -----------------------------------------------------------------------------


def test_time_dimension_has_one_row_per_minute():
    rows = time_rows()
    assert len(rows) == 1440
    assert rows[0]["time_key"] == 0 and rows[-1]["time_key"] == 2359
    pm = next(r for r in rows if r["time_key"] == 1405)
    assert (pm["hour_24"], pm["hour_12"], pm["minute"], pm["am_pm"]) == (14, 2, 5, "PM")
    assert pm["time_label"] == "14:05"
    assert next(c.name for c in time_columns()) == "time_key"


# --- seed files -----------------------------------------------------------------------


def test_seed_csv_has_a_header_and_iso_values():
    columns = date_columns(small())
    text = seed_csv(columns, date_rows(small())[:2])
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert parsed[0]["date_key"] == "20240101" and parsed[0]["full_date"] == "2024-01-01"
    assert parsed[0]["is_weekend"] == "false" and list(parsed[0]) == [c.name for c in columns]


def table_with_seed(settings: DateDimension) -> tuple[DdlTable, SeedData]:
    columns = date_columns(settings)
    table = DdlTable(
        id=1,
        layer="core",
        name="dim_date",
        kind="generated",
        columns=[DdlColumn(c.name, c.data_type, c.is_nullable, c.role, None) for c in columns],
    )
    return table, SeedData([c.name for c in columns], date_rows(settings)[:3])


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("postgresql", "DATE '2024-01-01'"),
        ("sqlserver", "'2024-01-01'"),
        ("oracle", "DATE '2024-01-01'"),
        ("snowflake", "DATE '2024-01-01'"),
        ("bigquery", "DATE '2024-01-01'"),
    ],
)
def test_the_package_seeds_generated_tables_per_platform(platform, expected):
    table, seed = table_with_seed(small())
    sql = generate_ddl(
        platform, {"staging": "s", "core": "core", "mart": "m"}, [table], seeds={table.id: seed}
    )
    assert "-- Seed data" in sql
    assert expected in sql
    assert "20240102" in sql
    assert sql.index("CREATE TABLE") < sql.index("-- Seed data")


def test_oracle_seeds_use_insert_all_and_sqlserver_batches_stay_under_1000_rows():
    table, _ = table_with_seed(small())
    seed = SeedData([c.name for c in table.columns], date_rows(small()))
    schemas = {"staging": "s", "core": "core", "mart": "m"}
    oracle = generate_ddl("oracle", schemas, [table], seeds={table.id: seed})
    assert "INSERT ALL" in oracle and "FROM DUAL;" in oracle
    mssql = generate_ddl("sqlserver", schemas, [table], seeds={table.id: seed})
    assert mssql.count("INSERT INTO") == 2  # 731 rows in batches of at most 500
    assert "CAST(" not in mssql


def test_booleans_render_per_platform():
    table, seed = table_with_seed(small(weekend_days=("friday",)))
    schemas = {"staging": "s", "core": "core", "mart": "m"}
    assert "FALSE" in generate_ddl("postgresql", schemas, [table], seeds={1: seed})
    assert "FALSE" not in generate_ddl("sqlserver", schemas, [table], seeds={1: seed})


# --- endpoints ------------------------------------------------------------------------


def set_up(roles: RoleClients, **date_dimension) -> None:
    body = {"target_platform": "postgresql"}
    if date_dimension:
        body["date_dimension"] = date_dimension
    response = roles.client("editor").post(base(roles), json=body)
    assert response.status_code == 201, response.text


def add_generated(roles: RoleClients, kind: str, role="editor"):
    return roles.client(role).post(f"{base(roles)}/tables/generated", json={"kind": kind})


def files_of(roles: RoleClients) -> dict[str, dict]:
    listing = roles.client("viewer").get(f"{base(roles)}/files").json()
    return {f["name"]: f for f in listing["items"]}


def download(roles: RoleClients, file: dict) -> str:
    url = f"/api/v1/workspaces/{roles.workspace_id}/files/{file['id']}/download"
    response = roles.client("viewer").get(url)
    assert response.status_code == 200
    return response.text


def test_an_editor_adds_the_date_dimension_from_the_warehouse_settings(roles: RoleClients):
    set_up(
        roles,
        start_year=2024,
        end_year=2025,
        weekend_days=["friday", "saturday"],
        include_hijri=True,
        fiscal_year_start_month=7,
    )

    response = add_generated(roles, "date")

    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["kind"], created["layer"], created["name"]) == ("generated", "core", "dim_date")
    assert created["is_conformed"] is True
    names = [c["name"] for c in created["columns"]]
    assert names[0] == "date_key"
    assert {"full_date", "is_weekend", "hijri_year", "fiscal_year"} <= set(names)
    key = created["columns"][0]
    assert (key["role"], key["data_type"]["type"], key["is_nullable"]) == ("sk", "integer", False)


def test_a_plain_date_dimension_has_no_hijri_or_fiscal_columns(roles: RoleClients):
    set_up(roles)
    names = [c["name"] for c in add_generated(roles, "date").json()["columns"]]
    assert "hijri_year" not in names and "fiscal_year" not in names and "year" in names


def test_the_time_dimension_is_optional_and_named_after_the_dimension_prefix(roles: RoleClients):
    set_up(roles)
    response = add_generated(roles, "time")
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "dim_time"
    assert response.json()["columns"][0]["name"] == "time_key"


def test_a_second_date_dimension_is_a_name_conflict(roles: RoleClients):
    set_up(roles)
    assert add_generated(roles, "date").status_code == 201
    assert add_generated(roles, "date").status_code == 409


def test_an_unknown_kind_is_refused_and_a_viewer_cannot_add(roles: RoleClients):
    set_up(roles)
    assert add_generated(roles, "moon").status_code == 422
    assert add_generated(roles, "date", role="viewer").status_code == 403


def test_a_fact_links_to_the_generated_date_dimension(roles: RoleClients):
    set_up(roles)
    dates = add_generated(roles, "date").json()
    sales = fact(roles)
    linked = column(
        roles,
        sales["id"],
        name="order_date_key",
        data_type={"type": "integer"},
        role="fk",
        references_table_id=dates["id"],
    )
    assert linked["references_table_id"] == dates["id"]


def test_the_ddl_package_seeds_the_generated_tables(roles: RoleClients):
    set_up(roles, start_year=2024, end_year=2024, include_hijri=True)
    add_generated(roles, "date")
    add_generated(roles, "time")

    sql = roles.client("viewer").get(f"{base(roles)}/ddl").text

    assert "CREATE TABLE core.dim_date" in sql and "PRIMARY KEY (date_key)" in sql
    assert "-- Seed data" in sql
    assert sql.count("INSERT INTO core.dim_date") == 1  # 366 rows in one batch
    assert "20240229" in sql and "'Ramadan'" in sql
    assert sql.count("INSERT INTO core.dim_time") == 3  # 1440 rows in batches of 500


def test_saving_the_package_also_saves_the_seed_files(roles: RoleClients):
    set_up(roles, start_year=2024, end_year=2024)
    add_generated(roles, "date")

    saved = roles.client("editor").post(f"{base(roles)}/files/ddl")

    assert saved.status_code == 201, saved.text
    files = files_of(roles)
    assert {"ddl-data-warehouse.sql", "seed-dim_date.csv", "seed-dim_date.sql"} <= set(files)
    parsed = list(csv.DictReader(io.StringIO(download(roles, files["seed-dim_date.csv"]))))
    assert len(parsed) == 366 and parsed[0]["date_key"] == "20240101"
    assert "hijri_year" not in parsed[0]
    script = download(roles, files["seed-dim_date.sql"])
    assert script.startswith("-- DAWAM seed data") and "INSERT INTO core.dim_date" in script
    assert "CREATE TABLE" not in script


def test_a_layer_package_seeds_only_that_layers_generated_tables(roles: RoleClients):
    set_up(roles, start_year=2024, end_year=2024)
    add_generated(roles, "date")
    table(roles, layer="mart", name="dim_region", kind="dimension")

    mart = roles.client("viewer").get(f"{base(roles)}/ddl", params={"layer": "mart"}).text

    assert "Seed data" not in mart
