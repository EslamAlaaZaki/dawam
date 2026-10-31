"""The committed database ERD (docs/architecture/database/erd.html) matches the schema.

The ERD is generated from a database migrated to head, so any schema change (a new
migration, or a docstring change in a module's ``tables.py``) must regenerate it in the
same change.
"""

from tools.generate_erd import ERD_PATH, check_enum_values, migrated_erd_html


def test_committed_erd_is_current(fresh_database_url):
    assert ERD_PATH.read_text(encoding="utf-8") == migrated_erd_html(fresh_database_url), (
        "docs/architecture/database/erd.html is stale. "
        "Run `python tools/generate_erd.py` in backend/ and commit the result."
    )


def test_check_constraints_yield_their_allowed_values():
    in_list = (
        "CHECK (((status)::text = ANY ((ARRAY['active'::character varying, "
        "'archived'::character varying])::text[])))"
    )
    assert list(check_enum_values(in_list, ["status"])) == [("status", ["active", "archived"])]

    numbers = "CHECK (((scd_type IS NULL) OR (scd_type = ANY (ARRAY[0, 1, 2]))))"
    assert list(check_enum_values(numbers, ["scd_type"])) == [("scd_type", ["0", "1", "2"])]

    subset = "CHECK ((roles <@ ARRAY['chat'::character varying, 'embedding'::character varying]))"
    assert list(check_enum_values(subset, ["roles"])) == [("roles", ["chat", "embedding"])]

    a_range = "CHECK (((progress >= 0) AND (progress <= 100)))"
    assert list(check_enum_values(a_range, ["progress"])) == []
