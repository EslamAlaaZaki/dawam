"""Staging names (spec §6.7): sanitising, placeholders, reserved words, collisions and
truncation per platform. Pure functions, driven by tables."""

from __future__ import annotations

import hashlib

import pytest

from dawam.modules.warehouse.platforms import TARGET_PLATFORMS, max_identifier_length
from dawam.modules.warehouse.staging_naming import (
    NameInput,
    column_names,
    needs_placeholder,
    sanitize,
    stable_hash,
    table_names,
)


def table(code: str, schema: str, name: str, key: str, placeholder: int | None = None):
    return (code, NameInput("s-" + schema, schema), NameInput(key, name, placeholder))


def one(platform, code, schema, name, placeholder=None, taken=()):
    result = table_names(
        platform, tables=[table(code, schema, name, "t1", placeholder)], taken=taken
    )
    return result["t1"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Customers", "customers"),
        ("Order Items", "order_items"),
        ("order-items", "order_items"),
        ("  Order.Items  ", "order_items"),
        ("__x__", "x"),
        ("Café", "cafe"),
        ("ORDERS$2024", "orders_2024"),
        ("客户", None),
        ("Kunde_客户", None),
        ("###", None),
    ],
)
def test_sanitising(raw, expected):
    assert sanitize(raw) == expected
    assert needs_placeholder(raw) is (expected is None)


def test_a_table_is_named_from_system_schema_and_table():
    named = one("postgresql", "CRM", "dbo", "Order Items")

    assert (named.name, named.flags) == ("stg_crm_dbo_order_items", ())


def test_a_non_latin_table_gets_its_stored_placeholder_number():
    named = one("postgresql", "crm", "dbo", "客户", placeholder=7)

    assert named.name == "stg_crm_dbo_tbl_007"
    assert [f.code for f in named.flags] == ["placeholder"]


def test_a_non_latin_table_without_a_number_is_a_programming_error():
    with pytest.raises(ValueError):
        one("postgresql", "crm", "dbo", "客户")


def test_a_non_latin_schema_falls_back_to_its_hash():
    named = one("postgresql", "crm", "数据", "orders")

    assert named.name == f"stg_crm_sch_{stable_hash('s-数据')}_orders"
    assert [f.code for f in named.flags] == ["placeholder"]


def test_the_hash_is_the_first_six_hex_of_sha256_of_the_object_id():
    assert stable_hash("abc") == hashlib.sha256(b"abc").hexdigest()[:6]


@pytest.mark.parametrize("platform", TARGET_PLATFORMS)
def test_names_over_the_platform_limit_are_truncated_with_a_hash(platform):
    limit = min(max_identifier_length(platform), 128)
    long_name = "x" * 400

    result = table_names(
        platform,
        tables=[table("crm", "dbo", long_name, "t1")],
        max_length=128,
    )["t1"]

    assert len(result.name.encode()) == limit
    assert result.name.endswith("_" + stable_hash("t1"))
    assert result.name.startswith("stg_crm_dbo_xxx")
    assert [f.code for f in result.flags] == ["truncated"]


def test_a_name_exactly_at_the_limit_is_kept():
    prefix = "stg_crm_dbo_"
    name = "y" * (63 - len(prefix))

    result = one("postgresql", "crm", "dbo", name)

    assert result.name == prefix + name and result.flags == ()


def test_postgresql_counts_bytes_not_characters():
    # 'é' folds to ASCII, so the limit is in plain bytes either way.
    result = one("postgresql", "crm", "dbo", "é" * 80)

    assert len(result.name.encode()) <= 63


def test_names_that_collide_after_sanitising_all_get_the_hash():
    result = table_names(
        "postgresql",
        tables=[
            table("crm", "dbo", "Order Items", "a"),
            table("crm", "dbo", "order_items", "b"),
            table("crm", "dbo", "Other", "c"),
        ],
    )

    assert result["a"].name == f"stg_crm_dbo_order_items_{stable_hash('a')}"
    assert result["b"].name == f"stg_crm_dbo_order_items_{stable_hash('b')}"
    assert result["c"].name == "stg_crm_dbo_other"
    assert [f.code for f in result["a"].flags] == ["collision"]


def test_case_sensitive_names_that_differ_only_in_case_collide():
    result = table_names(
        "postgresql",
        tables=[table("crm", "dbo", "Customer", "a"), table("crm", "dbo", "customer", "b")],
    )

    assert result["a"].name != result["b"].name
    assert all(r.name.startswith("stg_crm_dbo_customer_") for r in result.values())


def test_a_new_name_that_meets_an_existing_table_gets_the_hash_and_the_old_one_stays():
    result = one("postgresql", "crm", "dbo", "Customer", taken={"stg_crm_dbo_customer"})

    assert result.name == f"stg_crm_dbo_customer_{stable_hash('t1')}"
    assert [f.code for f in result.flags] == ["collision"]


def test_a_collision_across_system_codes_is_found():
    result = table_names(
        "postgresql",
        tables=[table("a_b", "c", "t", "x"), table("a", "b_c", "t", "y")],
    )

    assert result["x"].name != result["y"].name


def test_truncation_that_ends_in_the_same_prefix_stays_distinct():
    long_a, long_b = "z" * 100 + "a", "z" * 100 + "b"
    result = table_names(
        "postgresql",
        tables=[table("crm", "dbo", long_a, "a"), table("crm", "dbo", long_b, "b")],
    )

    assert result["a"].name != result["b"].name
    assert all(len(r.name) <= 63 for r in result.values())


def cols(platform, names, taken=()):
    inputs = [NameInput(f"c{i}", n, p) for i, (n, p) in enumerate(names)]
    out = column_names(platform, columns=inputs, taken=taken)
    return [out[f"c{i}"] for i in range(len(names))]


@pytest.mark.parametrize(
    ("platform", "raw", "expected"),
    [
        ("postgresql", "Order", "order_col"),
        ("postgresql", "OrderId", "orderid"),
        ("sqlserver", "Index", "index_col"),
        ("oracle", "Level", "level_col"),
        ("oracle", "Comment", "comment_col"),
        ("snowflake", "Qualify", "qualify_col"),
        ("bigquery", "Struct", "struct_col"),
        ("postgresql", "Index", "index"),
        ("postgresql", "1st place", "c_1st_place"),
        ("postgresql", "First Name", "first_name"),
    ],
)
def test_reserved_words_get_a_suffix(platform, raw, expected):
    [named] = cols(platform, [(raw, None)])

    assert named.name == expected


def test_a_non_latin_column_gets_its_stored_placeholder_number():
    [named] = cols("postgresql", [("名前", 17)])

    assert named.name == "col_017"
    assert [f.code for f in named.flags] == ["placeholder"]


def test_columns_that_collide_all_get_the_hash():
    a, b, c = cols("postgresql", [("First Name", None), ("first_name", None), ("age", None)])

    assert a.name == f"first_name_{stable_hash('c0')}"
    assert b.name == f"first_name_{stable_hash('c1')}"
    assert c.name == "age"
    assert [f.code for f in a.flags] == ["collision"]


def test_a_column_named_like_an_audit_column_gets_the_hash():
    [named] = cols("postgresql", [("LOAD_TS", None)], taken={"load_ts", "source_system"})

    assert named.name == f"load_ts_{stable_hash('c0')}"
    assert [f.code for f in named.flags] == ["collision"]


def test_a_long_column_name_is_truncated_with_the_hash():
    [named] = cols("postgresql", [("c" * 90, None)])

    assert len(named.name) == 63 and named.name.endswith(stable_hash("c0"))
    assert [f.code for f in named.flags] == ["truncated"]
