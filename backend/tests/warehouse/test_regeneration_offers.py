"""Which audited changes offer a regeneration (spec §6.9 "Regeneration", stories 98, 98a): the
generator's mandatory inputs only, and never a change whose origin is regeneration or
propagation. Pure: the offers themselves are tested in ``tests/assistant/test_regeneration``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import AuditedChange, audited_changes, record_audit
from dawam.modules.warehouse.regeneration import (
    _TABLES,
    mandatory_input,
    note_new_inputs,
    offer_reasons,
)

WORKSPACE = uuid.uuid4()


def change(
    entity_type: str,
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
    via: str = "user",
) -> AuditedChange:
    return AuditedChange(WORKSPACE, entity_type, str(uuid.uuid4()), via, old, new)


@pytest.mark.parametrize(
    ("audited", "reason"),
    [
        (change("kpi", None, {"name": "Revenue"}), "kpi"),
        (change("kpi", {"name": "Revenue"}, None), "kpi"),
        (change("kpi", {"definition": "a"}, {"definition": "b"}), "kpi"),
        (change("kpi", None, {"name": "Revenue"}, via="ai"), "kpi"),
        (
            change("source_table", {"classification": None}, {"classification": "master"}),
            "source_schema",
        ),
        (
            change("source_table", {"scd_hint": None}, {"scd_hint": "history"}, via="ai"),
            "source_schema",
        ),
        (change("relationship", {"status": "suggested"}, {"status": "accepted"}), "source_schema"),
    ],
)
def test_a_mandatory_input_change_offers_a_regeneration(audited: AuditedChange, reason: str):
    assert mandatory_input(audited) == reason


@pytest.mark.parametrize(
    "audited",
    [
        # What the generator does not read.
        change("kpi", {"links": []}, {"links": ["x"]}),
        change(
            "kpi",
            {"formula_sql": "a", "status": "approved"},
            {"formula_sql": "b", "status": "draft"},
        ),
        change("kpi", {"owner": "a"}, {"owner": "b"}),
        change("source_table", {"description": None}, {"description": "Customers"}),
        change("source_column", {"tags": []}, {"tags": ["pii"]}),
        change("dw_table", {"name": "a"}, {"name": "b"}),
        change("column_mapping", None, {"sql_expression": "x"}),
        # No loop: what regeneration or propagation changed.
        change("kpi", {"name": "a"}, {"name": "b"}, via="propagation"),
        change("kpi", None, {"name": "Revenue"}, via="regeneration"),
        change(
            "source_table",
            {"classification": None},
            {"classification": "master"},
            via="propagation",
        ),
        change("relationship", {"status": "suggested"}, {"status": "accepted"}, via="regeneration"),
    ],
)
def test_other_changes_offer_nothing(audited: AuditedChange):
    assert mandatory_input(audited) is None


def test_reasons_are_gathered_per_workspace():
    other = uuid.uuid4()
    changes = [
        change("kpi", None, {"name": "Revenue"}),
        change("source_table", {"description": None}, {"description": "x"}),
        AuditedChange(other, "relationship", "r", "user", None, {"status": "accepted"}),
        change("kpi", {"name": "a"}, {"name": "b"}, via="propagation"),
    ]

    assert offer_reasons(changes) == {WORKSPACE: ["kpi"], other: ["source_schema"]}


# -- what a transaction noted lives exactly as long as the transaction ----------------------


def test_a_savepoint_rollback_keeps_the_outer_notes_and_a_real_rollback_drops_them():
    engine = sa.create_engine("sqlite://")
    table_id = uuid.uuid4()
    with Session(engine) as db:
        db.execute(sa.text("select 1"))
        record_audit(
            db,
            workspace_id=WORKSPACE,
            actor_id=None,
            entity_type="kpi",
            entity_id=uuid.uuid4(),
            old=None,
            new={"name": "Revenue"},
            at=datetime.now(UTC),
        )
        db.expunge_all()  # no tables here: only what the session noted matters
        note_new_inputs(db, WORKSPACE, table_id)
        nested = db.begin_nested()
        nested.rollback()

        assert [c.entity_type for c in audited_changes(db)] == ["kpi"]
        assert db.info[_TABLES] == {(WORKSPACE, table_id)}

        db.rollback()

        assert audited_changes(db) == []
        assert _TABLES not in db.info
