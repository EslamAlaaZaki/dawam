"""The "Set up Data Warehouse" step (spec §6.9, story 87)."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse import (
    TARGET_PLATFORMS,
    is_reserved_word,
    max_identifier_length,
    platform_profile,
)
from tests.roles import RoleClients


def url(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


def set_up(roles: RoleClients, role="editor", **body):
    return roles.client(role).post(url(roles), json={"target_platform": "postgresql", **body})


def test_before_setup_the_workspace_has_no_data_warehouse(roles: RoleClients):
    response = roles.client("viewer").get(url(roles))

    assert response.status_code == 200
    assert response.json()["set_up"] is False
    assert response.json()["target_platform"] is None


def test_an_editor_sets_it_up_with_defaults(roles: RoleClients):
    response = set_up(roles)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["set_up"] is True
    assert body["target_platform"] == "postgresql"
    assert body["layer_schemas"] == {"staging": "staging", "core": "core", "mart": "mart"}
    assert body["naming_rules"]["dimension_prefix"] == "dim_"
    assert body["date_dimension"]["weekend_days"] == ["saturday", "sunday"]
    assert body["set_up_at"] is not None and body["version"] == 1
    assert roles.client("viewer").get(url(roles)).json() == body


def test_setup_records_the_chosen_settings(roles: RoleClients):
    response = set_up(
        roles,
        target_platform="bigquery",
        layer_schemas={"staging": "raw_ds", "core": "core_ds", "mart": "mart_ds"},
        naming_rules={"case_style": "upper", "dimension_prefix": "d_", "fact_prefix": "f_"},
        date_dimension={
            "start_year": 2015,
            "end_year": 2035,
            "weekend_days": ["friday", "saturday"],
            "include_hijri": True,
            "fiscal_year_start_month": 7,
            "include_time_dimension": True,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target_platform"] == "bigquery"
    assert body["layer_schemas"]["staging"] == "raw_ds"
    assert body["naming_rules"]["case_style"] == "upper"
    assert body["date_dimension"]["fiscal_year_start_month"] == 7
    assert body["date_dimension"]["weekend_days"] == ["friday", "saturday"]


def test_setting_up_twice_is_refused(roles: RoleClients):
    set_up(roles)

    response = set_up(roles, role="owner")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_set_up"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"layer_schemas": {"staging": "bad name"}}, "layer_schemas.staging"),
        ({"layer_schemas": {"staging": "1st"}}, "layer_schemas.staging"),
        ({"layer_schemas": {"staging": "select"}}, "layer_schemas.staging"),
        ({"layer_schemas": {"staging": "x" * 64}}, "layer_schemas.staging"),
        ({"layer_schemas": {"staging": "same", "core": "SAME"}}, "layer_schemas.core"),
        ({"naming_rules": {"dimension_prefix": "1d_"}}, "naming_rules.dimension_prefix"),
        ({"naming_rules": {"dimension_prefix": "x_", "fact_prefix": "X_"}}, "naming_rules"),
        ({"date_dimension": {"start_year": 2030, "end_year": 2020}}, "date_dimension.end_year"),
        (
            {"date_dimension": {"start_year": 1000, "end_year": 2000}},
            "date_dimension.end_year",
        ),
        (
            {"date_dimension": {"weekend_days": ["friday", "friday"]}},
            "date_dimension.weekend_days",
        ),
    ],
)
def test_invalid_settings_are_refused_and_name_the_field(roles: RoleClients, body, field):
    response = set_up(roles, **body)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_data_warehouse"
    assert error["details"]["field"] == field
    assert roles.client("viewer").get(url(roles)).json()["set_up"] is False


def test_schema_names_are_checked_against_the_chosen_platform(roles: RoleClients):
    long_name = "a" * 100

    refused = set_up(roles, layer_schemas={"staging": long_name})
    accepted = set_up(roles, target_platform="sqlserver", layer_schemas={"staging": long_name})

    assert refused.status_code == 422
    assert accepted.status_code == 201


def test_an_editor_edits_settings_but_not_the_platform(roles: RoleClients):
    version = set_up(roles).json()["version"]

    edited = roles.client("editor").patch(
        url(roles), json={"version": version, "naming_rules": {"case_style": "upper"}}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["naming_rules"]["case_style"] == "upper"
    assert edited.json()["version"] == version + 1
    assert edited.json()["target_platform"] == "postgresql"

    same_platform = roles.client("editor").patch(
        url(roles), json={"version": version + 1, "target_platform": "postgresql"}
    )
    assert same_platform.status_code == 200

    changed = roles.client("editor").patch(
        url(roles), json={"version": version + 2, "target_platform": "oracle"}
    )
    assert changed.status_code == 403


def test_an_owner_changes_the_platform(roles: RoleClients):
    version = set_up(roles).json()["version"]

    response = roles.client("owner").patch(
        url(roles), json={"version": version, "target_platform": "snowflake"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["target_platform"] == "snowflake"


def test_changing_the_platform_rechecks_the_names_against_it(roles: RoleClients):
    version = set_up(
        roles, target_platform="sqlserver", layer_schemas={"staging": "a" * 100}
    ).json()["version"]

    response = roles.client("owner").patch(
        url(roles), json={"version": version, "target_platform": "postgresql"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "layer_schemas.staging"


def test_editing_with_a_stale_version_conflicts(roles: RoleClients):
    version = set_up(roles).json()["version"]
    roles.client("owner").patch(url(roles), json={"version": version, "naming_rules": {}})

    response = roles.client("editor").patch(url(roles), json={"version": version})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


def test_editing_before_setup_is_404(roles: RoleClients):
    response = roles.client("owner").patch(url(roles), json={"version": 1})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_set_up"


def test_the_platform_endpoint_lists_limits_and_reserved_words(roles: RoleClients):
    response = roles.client("viewer").get("/api/v1/data-warehouse/platforms")

    assert response.status_code == 200
    items = {item["platform"]: item for item in response.json()["items"]}
    assert set(items) == set(TARGET_PLATFORMS)
    assert items["postgresql"]["max_identifier_length"] == 63
    assert items["oracle"]["max_identifier_length"] == 128
    assert items["bigquery"]["schema_term"] == "dataset"
    assert "select" in items["sqlserver"]["reserved_words"]


def test_other_modules_read_limits_and_reserved_words_from_the_package():
    assert max_identifier_length("postgresql") == 63
    assert max_identifier_length("sqlserver") == 128
    assert max_identifier_length("oracle") == 128
    assert is_reserved_word("postgresql", "USER")
    assert is_reserved_word("sqlserver", "Top")
    assert not is_reserved_word("postgresql", "top")
    assert is_reserved_word("oracle", "date")
    assert is_reserved_word("oracle", "COMMENT")
    assert is_reserved_word("snowflake", "view")
    assert is_reserved_word("bigquery", "unnest")
    assert platform_profile("snowflake").label == "Snowflake"


def test_the_service_rejects_an_unknown_case_style():
    from dawam.modules.warehouse import NamingRules
    from dawam.modules.warehouse.service import _Settings, _validate
    from dawam.platform.errors import ApiError

    rules = NamingRules(case_style="title")  # type: ignore[arg-type]
    with pytest.raises(ApiError) as caught:
        _validate(_Settings("postgresql", naming_rules=rules))
    assert caught.value.details == {"field": "naming_rules.case_style"}


# --- the audit column names (#617) ---------------------------------------------------------


def with_a_core_table(roles: RoleClients) -> dict:
    """Set up, then make one Core table by hand: it carries DAWAM's audit columns."""
    assert set_up(roles).status_code == 201
    response = roles.client("editor").post(
        f"{url(roles)}/tables", json={"layer": "core", "name": "dim_customer", "kind": "dimension"}
    )
    assert response.status_code == 201, response.text
    return roles.client("editor").get(url(roles)).json()


def change_rules(roles: RoleClients, current: dict, **rules):
    return roles.client("editor").patch(
        url(roles),
        json={"version": current["version"], "naming_rules": current["naming_rules"] | rules},
    )


@pytest.mark.parametrize("rule", ["created_ts_column", "updated_ts_column", "source_system_column"])
def test_an_audit_column_name_cannot_change_once_core_or_mart_tables_exist(
    roles: RoleClients, rule: str
):
    current = with_a_core_table(roles)

    response = change_rules(roles, current, **{rule: "etl_renamed"})

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "audit_names_in_use"
    assert error["details"]["fields"] == [f"naming_rules.{rule}"]
    after = roles.client("viewer").get(url(roles)).json()
    assert after["naming_rules"] == current["naming_rules"]


def test_audit_column_names_change_freely_before_any_core_or_mart_table(roles: RoleClients):
    assert set_up(roles).status_code == 201
    current = roles.client("editor").get(url(roles)).json()

    response = change_rules(
        roles,
        current,
        created_ts_column="etl_created",
        updated_ts_column="etl_updated",
        source_system_column="etl_source",
    )

    assert response.status_code == 200, response.text
    assert response.json()["naming_rules"]["created_ts_column"] == "etl_created"


def test_unchanged_audit_names_and_other_rules_still_save_with_tables(roles: RoleClients):
    current = with_a_core_table(roles)

    response = change_rules(roles, current, case_style="upper", fact_prefix="fct_")

    assert response.status_code == 200, response.text
    rules = response.json()["naming_rules"]
    assert rules["fact_prefix"] == "fct_" and rules["case_style"] == "upper"
    assert rules["created_ts_column"] == "dw_created_ts"


def test_the_staging_load_ts_name_still_changes_with_core_tables(roles: RoleClients):
    current = with_a_core_table(roles)

    response = change_rules(roles, current, load_ts_column="etl_loaded_at")

    assert response.status_code == 200, response.text
    assert response.json()["naming_rules"]["load_ts_column"] == "etl_loaded_at"


def test_a_table_made_while_the_rules_change_takes_the_new_audit_names(roles: RoleClients):
    import threading

    import sqlalchemy as sa

    assert set_up(roles).status_code == 201
    engine = roles.app.state.engine
    made: dict = {}
    with engine.connect() as connection:
        change = connection.begin()  # a rule change in flight holds the row, as update does
        connection.execute(
            sa.text(
                "update data_warehouses set naming_rules = (naming_rules::jsonb "
                '|| \'{"created_ts_column": "etl_created"}\'::jsonb)::json '
                "where workspace_id = :w"
            ),
            {"w": roles.workspace_id},
        )
        creating = threading.Thread(
            target=lambda: made.update(
                response=roles.client("editor").post(
                    f"{url(roles)}/tables",
                    json={"layer": "core", "name": "dim_customer", "kind": "dimension"},
                )
            )
        )
        creating.start()
        creating.join(2)
        assert creating.is_alive()  # the create waits for the change
        change.commit()
    creating.join(60)

    assert made["response"].status_code == 201, made["response"].text
    names = [c["name"] for c in made["response"].json()["columns"]]
    assert "etl_created" in names and "dw_created_ts" not in names
