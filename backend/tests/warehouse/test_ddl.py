"""DDL generation (spec §6.9, story 97): the pure generator, table-driven per platform with
golden files, then the endpoints that download and save a package."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from dawam.modules.warehouse import TARGET_PLATFORMS
from dawam.modules.warehouse.ddl import DdlColumn, DdlTable, generate_ddl, ident, translate_type
from dawam.modules.warehouse.model_service import NEUTRAL_TYPES
from tests.roles import RoleClients
from tests.warehouse.test_core_mart_model import base, column, dimension, fact

GOLDEN = Path(__file__).parent / "golden"
SCHEMAS = {"staging": "stg", "core": "core", "mart": "mart"}

# --- type translation -----------------------------------------------------------------

# neutral type -> expected per platform: postgresql, sqlserver, oracle, snowflake, bigquery
TYPE_TABLE = [
    ({"type": "smallint"}, ("SMALLINT", "SMALLINT", "NUMBER(5)", "SMALLINT", "INT64")),
    ({"type": "integer"}, ("INTEGER", "INT", "NUMBER(10)", "INTEGER", "INT64")),
    ({"type": "bigint"}, ("BIGINT", "BIGINT", "NUMBER(19)", "BIGINT", "INT64")),
    (
        {"type": "decimal", "precision": 12, "scale": 2},
        ("NUMERIC(12,2)", "DECIMAL(12,2)", "NUMBER(12,2)", "NUMBER(12,2)", "NUMERIC(12,2)"),
    ),
    ({"type": "decimal"}, ("NUMERIC", "DECIMAL(38,10)", "NUMBER", "NUMBER(38,0)", "NUMERIC")),
    (
        {"type": "decimal", "precision": 38, "scale": 20},
        ("NUMERIC(38,20)", "DECIMAL(38,20)", "NUMBER(38,20)", "NUMBER(38,20)", "BIGNUMERIC(38,20)"),
    ),
    ({"type": "float"}, ("REAL", "REAL", "BINARY_FLOAT", "FLOAT", "FLOAT64")),
    ({"type": "double"}, ("DOUBLE PRECISION", "FLOAT(53)", "BINARY_DOUBLE", "DOUBLE", "FLOAT64")),
    ({"type": "boolean"}, ("BOOLEAN", "BIT", "NUMBER(1)", "BOOLEAN", "BOOL")),
    ({"type": "char", "length": 3}, ("CHAR(3)", "NCHAR(3)", "CHAR(3)", "CHAR(3)", "STRING(3)")),
    (
        {"type": "string", "length": 50},
        ("VARCHAR(50)", "NVARCHAR(50)", "VARCHAR2(50)", "VARCHAR(50)", "STRING(50)"),
    ),
    (
        {"type": "string", "length": 5000},
        ("VARCHAR(5000)", "NVARCHAR(MAX)", "CLOB", "VARCHAR(5000)", "STRING(5000)"),
    ),
    ({"type": "text"}, ("TEXT", "NVARCHAR(MAX)", "CLOB", "VARCHAR", "STRING")),
    (
        {"type": "binary", "length": 16},
        ("BYTEA", "VARBINARY(16)", "RAW(16)", "BINARY(16)", "BYTES(16)"),
    ),
    ({"type": "date"}, ("DATE", "DATE", "DATE", "DATE", "DATE")),
    ({"type": "time"}, ("TIME", "TIME", "INTERVAL DAY(0) TO SECOND(0)", "TIME", "TIME")),
    (
        {"type": "timestamp"},
        ("TIMESTAMP", "DATETIME2", "TIMESTAMP", "TIMESTAMP_NTZ", "DATETIME"),
    ),
    (
        {"type": "timestamptz"},
        ("TIMESTAMPTZ", "DATETIMEOFFSET", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_TZ", "TIMESTAMP"),
    ),
    ({"type": "uuid"}, ("UUID", "UNIQUEIDENTIFIER", "RAW(16)", "VARCHAR(36)", "STRING")),
    ({"type": "json"}, ("JSONB", "NVARCHAR(MAX)", "CLOB", "VARIANT", "JSON")),
]


@pytest.mark.parametrize("neutral,expected", TYPE_TABLE, ids=lambda v: str(v)[:40])
def test_neutral_types_translate_for_all_five_platforms(neutral, expected):
    assert tuple(translate_type(p, neutral) for p in TARGET_PLATFORMS) == expected


def test_the_type_table_covers_every_neutral_type():
    assert {t["type"] for t, _ in TYPE_TABLE} == set(NEUTRAL_TYPES)


@pytest.mark.parametrize(
    "platform,name,expected",
    [
        ("postgresql", "dim_customer", "dim_customer"),
        ("postgresql", "user", '"user"'),
        ("postgresql", "has space", '"has space"'),
        ("sqlserver", "order", "[order]"),
        ("bigquery", "group", "`group`"),
        ("oracle", 'a"b', '"a""b"'),
    ],
)
def test_identifiers_are_quoted_only_when_unsafe(platform, name, expected):
    assert ident(platform, name) == expected


# --- golden files ---------------------------------------------------------------------


def col(name, kind, nullable=False, role="attribute", **type_args):
    return DdlColumn(name, {"type": kind, **type_args}, nullable, role)


CUSTOMER = uuid.UUID(int=1)
PRODUCT = uuid.UUID(int=2)


def sample_model() -> list[DdlTable]:
    dim_customer = DdlTable(
        CUSTOMER,
        "core",
        "dim_customer",
        "dimension",
        [
            col("customer_key", "bigint", role="sk"),
            col("customer_id", "string", role="nk", length=20),
            col("full_name", "string", length=200),
            col("segment", "char", nullable=True, length=1),
            col("birth_date", "date", nullable=True),
            col("is_vip", "boolean"),
            col("credit_limit", "decimal", nullable=True, precision=12, scale=2),
            col("profile", "json", nullable=True),
            col("scd_valid_from", "timestamp", role="scd_valid_from"),
            col("scd_valid_to", "timestamp", nullable=True, role="scd_valid_to"),
            col("scd_current_flag", "boolean", role="scd_current_flag"),
            col("row_hash", "string", role="row_hash", length=64),
        ],
        {"surrogate_key": -1, "defaults": {"full_name": "Unknown customer's name", "segment": "?"}},
    )
    dim_product = DdlTable(
        PRODUCT,
        "core",
        "dim_product",
        "dimension",
        [
            col("product_key", "bigint", role="sk"),
            col("product_code", "string", role="nk", length=30),
            col("weight", "double"),
            col("launched_at", "timestamptz"),
            col("opens_at", "time"),
            col("guid", "uuid"),
            col("blob", "binary", length=16),
            col("order", "integer"),
            col("doc", "json"),
        ],
        {"surrogate_key": -1, "defaults": {"weight": 1.5}},
    )
    fact_sales = DdlTable(
        uuid.UUID(int=3),
        "core",
        "fact_sales",
        "fact",
        [
            DdlColumn("customer_key", {"type": "bigint"}, False, "fk", CUSTOMER),
            DdlColumn("product_key", {"type": "bigint"}, False, "fk", PRODUCT),
            col("invoice_no", "string", role="degenerate_dimension", length=20),
            col("quantity", "integer", role="measure"),
            col("amount", "decimal", role="measure", precision=18, scale=2),
        ],
    )
    monthly = DdlTable(
        uuid.UUID(int=4),
        "mart",
        "fact_sales_monthly",
        "fact",
        [
            DdlColumn("customer_key", {"type": "bigint"}, False, "fk", CUSTOMER),
            col("month", "date", role="degenerate_dimension"),
            col("total", "decimal", role="measure", precision=18, scale=2),
        ],
    )
    staging = DdlTable(
        uuid.UUID(int=5),
        "staging",
        "stg_orders",
        "staging",
        [col("id", "bigint"), col("note", "text", nullable=True)],
    )
    return [monthly, fact_sales, dim_product, staging, dim_customer]


def check_golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


@pytest.mark.parametrize("platform", TARGET_PLATFORMS)
def test_whole_dw_package_matches_golden_file(platform):
    check_golden(f"{platform}_dw.sql", generate_ddl(platform, SCHEMAS, sample_model()))


@pytest.mark.parametrize("platform", TARGET_PLATFORMS)
def test_single_layer_package_matches_golden_file(platform):
    ddl = generate_ddl(platform, SCHEMAS, sample_model(), layers=["core"])
    check_golden(f"{platform}_core.sql", ddl)


def test_a_layer_package_holds_only_that_layer_and_no_foreign_keys_out_of_it():
    ddl = generate_ddl("postgresql", SCHEMAS, sample_model(), layers=["mart"])
    assert "fact_sales_monthly" in ddl
    assert "dim_customer" not in ddl
    assert "FOREIGN KEY" not in ddl
    assert "INSERT" not in ddl


def test_every_dimension_gets_an_unknown_member_insert_with_key_minus_one():
    ddl = generate_ddl("postgresql", SCHEMAS, sample_model())
    assert ddl.count("INSERT INTO") == 2
    assert ddl.count("VALUES (-1,") == 2
    assert "'Unknown customer''s name'" in ddl


def test_generation_is_deterministic():
    model = sample_model()
    assert generate_ddl("oracle", SCHEMAS, model) == generate_ddl("oracle", SCHEMAS, model[::-1])


def test_a_long_constraint_name_is_shortened_with_a_stable_hash():
    target = DdlTable(1, "core", "d", "dimension", [col("k", "bigint", role="sk")], {})
    source = DdlTable(
        2, "core", "f" * 40, "fact", [DdlColumn("c" * 40, {"type": "bigint"}, False, "fk", 1)]
    )
    ddl = generate_ddl("postgresql", SCHEMAS, [target, source])
    [line] = [line for line in ddl.splitlines() if "ADD CONSTRAINT" in line]
    name = line.split("ADD CONSTRAINT ")[1].split(" ")[0]
    assert len(name) <= 63
    assert ddl == generate_ddl("postgresql", SCHEMAS, [target, source])


# --- endpoints ------------------------------------------------------------------------


@pytest.fixture
def modelled(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    customers = dimension(roles, "dim_customer")
    column(
        roles,
        customers["id"],
        name="customer_id",
        data_type={"type": "string", "length": 20},
        role="nk",
    )
    sales = fact(roles)
    column(
        roles,
        sales["id"],
        name="customer_key",
        data_type={"type": "bigint"},
        role="fk",
        references_table_id=customers["id"],
    )
    dimension(roles, "dim_region", layer="mart")
    return roles


def ddl_url(roles: RoleClients) -> str:
    return f"{base(roles)}/ddl"


def test_a_member_downloads_the_whole_dw_package(modelled: RoleClients):
    response = modelled.client("viewer").get(ddl_url(modelled))

    assert response.status_code == 200, response.text
    assert "ddl-data-warehouse.sql" in response.headers["content-disposition"]
    sql = response.text
    assert "CREATE SCHEMA IF NOT EXISTS core;" in sql
    assert "CREATE SCHEMA IF NOT EXISTS mart;" in sql
    assert "CREATE TABLE core.dim_customer" in sql
    assert "FOREIGN KEY (customer_key) REFERENCES core.dim_customer" in sql
    assert sql.count("INSERT INTO") == 2


def test_a_layer_package_is_one_layers_tables(modelled: RoleClients):
    response = modelled.client("viewer").get(ddl_url(modelled), params={"layer": "mart"})

    assert response.status_code == 200, response.text
    assert "ddl-mart.sql" in response.headers["content-disposition"]
    assert "mart.dim_region" in response.text
    assert "core.dim_customer" not in response.text


def test_ddl_before_set_up_is_404(roles: RoleClients):
    assert roles.client("viewer").get(ddl_url(roles)).status_code == 404


def test_an_editor_saves_the_package_to_the_file_area_and_it_downloads(modelled: RoleClients):
    saved = modelled.client("editor").post(f"{base(modelled)}/files/ddl")

    assert saved.status_code == 201, saved.text
    file = saved.json()
    assert file["name"] == "ddl-data-warehouse.sql"
    assert file["owner_kind"] == "data_warehouse"
    listing = modelled.client("viewer").get(f"{base(modelled)}/files").json()
    assert "ddl-data-warehouse.sql" in [f["name"] for f in listing["items"]]
    download = modelled.client("viewer").get(
        f"/api/v1/workspaces/{modelled.workspace_id}/files/{file['id']}/download"
    )
    assert download.status_code == 200
    assert "CREATE TABLE core.dim_customer" in download.text


def test_saving_a_layer_package_names_the_file_after_the_layer(modelled: RoleClients):
    saved = modelled.client("editor").post(f"{base(modelled)}/files/ddl", params={"layer": "core"})

    assert saved.status_code == 201, saved.text
    assert saved.json()["name"] == "ddl-core.sql"


def test_a_viewer_cannot_save_the_package(modelled: RoleClients):
    assert modelled.client("viewer").post(f"{base(modelled)}/files/ddl").status_code == 403
