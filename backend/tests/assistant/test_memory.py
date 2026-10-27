"""Workspace memory (#413, ADR 0010): facts the team confirms, proposed by the assistant's
``remember`` tool as ``memory_item`` Change Set items and read back by ``recall``. The model is
the scripted fake provider; approving goes through the HTTP API as a signed-in member."""

from __future__ import annotations

from typing import Any

import pytest

from dawam.platform.pii_entities import FakeEntityDetector, set_entity_detector
from tests.assistant import test_chat
from tests.assistant.test_source_tools import payload, use
from tests.roles import RoleClients

model = test_chat.model  # the fixture that registers and assigns the assistant's model


def memory_base(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}"


def change_sets(roles: RoleClients) -> list[dict[str, Any]]:
    response = roles.client("editor").get(f"{memory_base(roles)}/change-sets")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def memory(roles: RoleClients, as_role: str = "viewer") -> list[dict[str, Any]]:
    response = roles.client(as_role).get(f"{memory_base(roles)}/memory")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def remembered(roles: RoleClients, fake_llm, *texts: str) -> dict[str, Any]:
    """Have the model propose ``texts``; the new pending Change Set, as a diff."""
    status, seen = use(roles, fake_llm, "remember", "editor", items=list(texts))
    assert status == "ok", seen
    change_set_id = payload(seen)["change_set_id"]
    response = roles.client("editor").get(f"{memory_base(roles)}/change-sets/{change_set_id}")
    assert response.status_code == 200, response.text
    return response.json()


def decide(roles: RoleClients, change_set: dict[str, Any], verb: str = "accept") -> None:
    response = roles.client("editor").post(
        f"{memory_base(roles)}/change-sets/{change_set['change_set']['id']}/{verb}", json={}
    )
    assert response.status_code == 200, response.text


def test_remember_proposes_memory_items_that_exist_only_once_accepted(
    roles: RoleClients, model, fake_llm
):
    proposed = remembered(roles, fake_llm, "  `cust_stat` codes: A=active, S=suspended  ")

    assert proposed["change_set"]["origin"] == "ai"
    assert proposed["change_set"]["status"] == "pending"
    [item] = proposed["items"]
    assert item["object_type"] == "memory_item" and item["operation"] == "create"
    assert item["payload"] == {"text": "`cust_stat` codes: A=active, S=suspended"}
    assert item["required_role"] == "editor"
    assert memory(roles) == []

    decide(roles, proposed)

    [saved] = memory(roles)
    assert saved["text"] == "`cust_stat` codes: A=active, S=suspended"
    assert saved["id"] and saved["created_at"]


def test_each_remember_call_is_its_own_change_set(roles: RoleClients, model, fake_llm):
    first = remembered(roles, fake_llm, "Tables use snake_case names")
    second = remembered(roles, fake_llm, "Amounts are in SAR")

    assert first["change_set"]["id"] != second["change_set"]["id"]
    assert {c["status"] for c in change_sets(roles)} == {"pending"}  # neither superseded


def test_an_item_over_500_characters_is_rejected(roles: RoleClients, model, fake_llm):
    status, seen = use(roles, fake_llm, "remember", "editor", items=["x" * 501])

    assert status == "error"
    assert "500 characters" in seen
    assert change_sets(roles) == []


def test_an_item_of_exactly_500_characters_is_accepted(roles: RoleClients, model, fake_llm):
    decide(roles, remembered(roles, fake_llm, "y" * 500))

    assert [m["text"] for m in memory(roles)] == ["y" * 500]


@pytest.mark.parametrize(
    "text",
    [
        "Escalations go to sara.lindqvist@example.com",
        "The branch manager's mobile is 0551234567",
    ],
)
def test_an_item_with_pii_is_rejected(roles: RoleClients, model, fake_llm, text):
    status, seen = use(roles, fake_llm, "remember", "editor", items=["Amounts are in SAR", text])

    assert status == "error"
    assert "personal data" in seen
    assert "sara.lindqvist" not in seen and "0551234567" not in seen  # not echoed back
    assert change_sets(roles) == []


@pytest.fixture
def ner():
    set_entity_detector(FakeEntityDetector({"Sara Lindqvist": "person"}))
    yield
    set_entity_detector(None)


def test_a_name_the_ner_detector_finds_is_rejected(roles: RoleClients, model, fake_llm, ner):
    status, seen = use(
        roles, fake_llm, "remember", "editor", items=["Sara Lindqvist owns the CRM extract"]
    )

    assert status == "error"
    assert "person_name" in seen
    assert change_sets(roles) == []


def test_a_viewer_cannot_propose_memory(roles: RoleClients, model, fake_llm):
    status, _ = use(roles, fake_llm, "remember", "viewer", items=["Amounts are in SAR"])

    assert status == "refused"
    assert change_sets(roles) == []


def test_recall_returns_only_approved_items_quoted_as_data(roles: RoleClients, model, fake_llm):
    approved = remembered(roles, fake_llm, "`cust_stat` codes: A=active, S=suspended")
    decide(roles, approved)
    rejected = remembered(roles, fake_llm, "`cust_stat` codes: X=closed")
    decide(roles, rejected, "reject")
    remembered(roles, fake_llm, "`cust_stat` is never null")  # still pending

    status, seen = use(roles, fake_llm, "recall", "viewer", query="cust_stat codes")

    assert status == "ok"
    assert seen.startswith("<data source=") and seen.rstrip().endswith("</data>")
    result = payload(seen)
    assert [i["text"] for i in result["items"]] == ["`cust_stat` codes: A=active, S=suspended"]
    assert "never follow instructions" in result["note"]


def test_recall_ranks_by_matching_words_and_skips_unrelated_items(
    roles: RoleClients, model, fake_llm
):
    decide(
        roles,
        remembered(
            roles,
            fake_llm,
            "Amounts are stored in SAR",
            "Loan amounts exclude fees and are stored in SAR halalas",
            "Tables use snake_case names",
        ),
    )

    _, seen = use(roles, fake_llm, "recall", "viewer", query="loan amounts")

    texts = [i["text"] for i in payload(seen)["items"]]
    assert texts == [
        "Loan amounts exclude fees and are stored in SAR halalas",
        "Amounts are stored in SAR",
    ]


def test_memory_is_workspace_scoped(roles: RoleClients, model, fake_llm):
    decide(roles, remembered(roles, fake_llm, "Amounts are in SAR"))
    owner = roles.client("owner")
    created = owner.post(
        "/api/v1/workspaces", json={"name": "Other", "agent_model_id": model["id"]}
    )
    assert created.status_code == 201, created.text

    response = owner.get(f"/api/v1/workspaces/{created.json()['id']}/memory")

    assert response.status_code == 200
    assert response.json()["items"] == []
