"""The ``suggest_kpis`` assistant tool (spec §6.13, stories 75, 76): suggested KPIs waiting
for review, create-only, audited as ``via=ai``. The model is the scripted fake provider."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from dawam.modules.audit import AuditService
from dawam.modules.llm import FakeAdapter, Reply
from tests.assistant import test_chat
from tests.assistant.test_chat import add_kpi, add_system, ask, conversation, tool, tool_names
from tests.assistant.test_source_tools import payload
from tests.authz.matrix import dw_model_table, snapshot_id, system_id
from tests.roles import RoleClients

model = test_chat.model
settings = test_chat.settings

IBAN = "SA0380000000608010167519"


def kpis(roles: RoleClients) -> list[dict]:
    response = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis", params={"review": "all"}
    )
    return response.json()["items"]


def suggest(
    roles: RoleClients,
    fake_llm: FakeAdapter,
    items: list[dict[str, Any]],
    role: str = "editor",
    **arguments: Any,
) -> tuple[str, str]:
    if "system_id" not in arguments:  # the Data Warehouse's are cross-system
        model_table = dw_model_table(roles)
        items = [{"based_on": [model_table], **item} for item in items]
    fake_llm.calls.clear()
    call = tool("suggest_kpis", suggestions=items, **arguments)
    fake_llm.script(Reply(tool_calls=(call,)), Reply(text="done"))
    client = roles.client(role)
    events = ask(client, roles, conversation(client, roles), "Suggest KPIs")
    [(_, shown)] = [e for e in events if e[0] == "tool"]
    return shown["status"], fake_llm.calls[1].messages[-1].content or ""


def idea(name: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": name,
        "definition": "How much the bank earns",
        "rationale": "The ledger has interest columns.",
        **fields,
    }


def test_suggestions_wait_in_suggested_with_a_rationale(roles, model, fake_llm):
    snapshot_id(roles)  # the Source System's one table: public.accounts
    system = str(system_id(roles))

    status, seen = suggest(
        roles,
        fake_llm,
        [idea("Net interest margin", unit="%", based_on=["public.accounts"])],
        system_id=system,
    )

    assert status == "ok"
    assert payload(seen)["created"][0]["name"] == "Net interest margin"
    [kpi] = kpis(roles)
    assert kpi["origin"] == "ai" and kpi["status"] == "draft"
    assert kpi["review_state"] == "suggested"
    assert kpi["rationale"] == "The ledger has interest columns."
    assert kpi["source_system_id"] == system and kpi["unit"] == "%"
    assert kpi["based_on"] == ["public.accounts"]


def test_a_suggestion_based_on_tables_outside_the_system_is_dropped(roles, model, fake_llm):
    snapshot_id(roles)
    system = str(system_id(roles))

    status, seen = suggest(
        roles,
        fake_llm,
        [
            idea("Accounts opened", based_on=["Public.ACCOUNTS"]),
            idea("Churn", based_on=["crm.customers"]),
            idea("No tables"),
        ],
        system_id=system,
    )

    assert status == "ok"
    result = payload(seen)
    assert [k["name"] for k in result["created"]] == ["Accounts opened"]
    assert result["created"][0]["link"].endswith("/kpis&tab=suggested")
    assert result["dropped"] == ["Churn", "No tables"]
    [kpi] = kpis(roles)
    assert kpi["based_on"] == ["public.accounts"]


def test_called_first_without_suggestions_it_lists_every_kpi_of_the_scope(roles, model, fake_llm):
    system = add_system(roles)
    owner = roles.client("owner")
    base = f"/api/v1/workspaces/{roles.workspace_id}/kpis"
    for name in ("Kept", "Turned down"):
        response = owner.post(base, json={"name": name, "source_system_id": system})
        assert response.status_code == 201, response.text
    add_kpi(roles, "Warehouse KPI")
    fake_llm.calls.clear()
    fake_llm.script(Reply(tool_calls=(tool("suggest_kpis", system_id=system),)), Reply(text="done"))
    client = roles.client("editor")

    ask(client, roles, conversation(client, roles), "Suggest KPIs for Core")

    result = payload(fake_llm.calls[1].messages[-1].content or "")
    assert {k["name"] for k in result["existing_kpis"]} == {"Kept", "Turned down"}
    assert {k["review_state"] for k in result["existing_kpis"]} == {"live"}
    assert "rejected" in result["note"]


def test_existing_kpis_are_never_changed_or_duplicated(roles, model, fake_llm):
    add_kpi(roles, "Net Revenue")
    before = kpis(roles)

    status, seen = suggest(
        roles,
        fake_llm,
        [idea("net revenue ", definition="overwrite!"), idea("Churn"), idea("CHURN")],
    )

    assert status == "ok"
    result = payload(seen)
    assert [k["name"] for k in result["created"]] == ["Churn"]
    assert result["skipped"] == ["net revenue", "CHURN"]
    after = {k["name"]: k for k in kpis(roles)}
    assert set(after) == {"Net Revenue", "Churn"}
    assert after["Net Revenue"] == before[0]  # untouched, version included
    assert after["Churn"]["origin"] == "ai"


def test_each_kpi_gets_one_audit_entry_via_ai(roles, model, fake_llm, app: FastAPI):
    suggest(roles, fake_llm, [idea("Churn"), idea("Cost to income")])

    entries = []
    for kpi in kpis(roles):
        entries += AuditService(app.state.engine).list(
            roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
        )
    assert len(entries) == 2
    assert {e.via for e in entries} == {"ai"}
    assert all(e.actor_id == roles.user("editor").id and e.old is None for e in entries)
    assert all(e.new and e.new["origin"] == "ai" and e.new["rationale"] for e in entries)


def test_accepted_ai_kpis_can_be_edited_and_deleted_by_an_editor(roles, model, fake_llm):
    suggest(roles, fake_llm, [idea("Churn")])
    [kpi] = kpis(roles)
    path = f"/api/v1/workspaces/{roles.workspace_id}/kpis/{kpi['id']}"
    editor = roles.client("editor")
    accepted = editor.post(f"{path}/accept").json()

    edited = editor.patch(path, json={"version": accepted["version"], "unit": "%"})

    assert edited.status_code == 200 and edited.json()["origin"] == "ai"
    assert editor.delete(path).status_code == 204
    # a deleted suggestion is rejected, not gone, so it never comes back on its own
    assert editor.get(path).json()["review_state"] == "rejected"


def test_model_written_text_is_redacted_with_the_validators(roles, model, fake_llm):
    suggest(
        roles,
        fake_llm,
        [idea("Churn", rationale=f"Seen on account {IBAN}", definition="Mail a@b.sa daily")],
    )

    [kpi] = kpis(roles)
    assert kpi["rationale"] == "Seen on account [redacted: iban]"
    assert kpi["definition"] == "Mail [redacted: email] daily"
    assert IBAN not in str(kpi)


@pytest.mark.parametrize(("role", "allowed"), [("viewer", False), ("editor", True)])
def test_only_roles_that_may_edit_kpis_are_offered_and_can_run_it(
    roles, model, fake_llm, role, allowed
):
    status, _ = suggest(roles, fake_llm, [idea("Churn")], role=role)

    assert status == ("ok" if allowed else "refused")
    assert ("suggest_kpis" in tool_names(fake_llm)) is allowed
    assert len(kpis(roles)) == (1 if allowed else 0)


def test_a_source_system_outside_the_workspace_is_refused(roles, model, fake_llm):
    status, _ = suggest(
        roles, fake_llm, [idea("Churn")], system_id="00000000-0000-4000-8000-000000000000"
    )

    assert status == "error" and kpis(roles) == []
