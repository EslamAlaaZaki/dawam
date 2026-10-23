"""The admin run explorer through the HTTP API (ticket 27.2)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from dawam.modules.llm import Reply, Usage

from .test_chat import (  # noqa: F401  (model: fixture)
    add_kpi,
    ask,
    base,
    conversation,
    done,
    model,
    tool,
)
from .test_feedback import rate

RUNS = "/api/v1/admin/assistant/runs"
SECRET = "jane.doe@example.com"


def run_one(roles, fake_llm, question="What is Net Revenue?"):
    add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=[tool("list_kpis")], usage=Usage(10, 2)),
        Reply(text="It is Net Revenue.", usage=Usage(20, 8)),
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles, title="Explore")
    owner.patch(f"{base(roles)}/conversations/{cid}", json={"shared_with_workspace": True})
    run = done(ask(owner, roles, cid, question))["run"]
    return cid, run


def test_only_admins_can_open_the_explorer(roles, model, fake_llm):  # noqa: F811
    _, run = run_one(roles, fake_llm)
    for who in ("owner", "editor", "viewer"):
        client = roles.client(who)
        assert client.get(RUNS).status_code == 403
        assert client.get(f"{RUNS}/{run['id']}").status_code == 403
    assert roles.client("anonymous").get(RUNS).status_code in (401, 403)
    assert roles.client("admin").get(RUNS).status_code == 200


def test_the_timeline_matches_the_stored_steps(roles, model, fake_llm):  # noqa: F811
    _, run = run_one(roles, fake_llm)
    admin = roles.client("admin")

    detail = admin.get(f"{RUNS}/{run['id']}").json()

    timeline = detail["timeline"]
    assert [(s["kind"], s["name"]) for s in timeline] == [
        ("model", "assistant-model"),
        ("tool", "list_kpis"),
    ]
    assert [s["status"] for s in timeline[1:]] == [c["status"] for c in run["tool_calls"]]
    assert timeline[1]["duration_ms"] == run["tool_calls"][0]["duration_ms"]
    assert (timeline[0]["input_tokens"], timeline[0]["output_tokens"]) == (
        run["input_tokens"],
        run["output_tokens"],
    )
    assert detail["run"]["prompt_version"]
    assert detail["run"]["tool_call_count"] == 1
    assert detail["run"]["member_email"] == roles.user("owner").email


def test_prompts_are_shown_redacted(roles, model, fake_llm):  # noqa: F811
    _, run = run_one(roles, fake_llm, question=f"Mail the result to {SECRET} please")
    admin = roles.client("admin")

    detail = admin.get(f"{RUNS}/{run['id']}").json()

    assert detail["timeline"][0]["prompt"].startswith("Mail the result to")
    assert SECRET not in detail["timeline"][0]["prompt"]
    assert SECRET not in admin.get(RUNS).text


def test_cost_comes_from_the_model_price_and_is_unknown_without_one(
    roles,
    model,  # noqa: F811
    fake_llm,
    admin_client,
):
    _, run = run_one(roles, fake_llm)
    admin = roles.client("admin")
    assert admin.get(f"{RUNS}/{run['id']}").json()["run"]["cost"] is None

    from tests.llm.test_roles_budgets import BASE

    priced = admin_client.put(
        f"{BASE}/prices",
        json={
            "model_name": model["name"],
            "currency": "USD",
            "input_per_million": "3",
            "output_per_million": "15",
            "cached_input_per_million": "0.3",
        },
    )
    assert priced.status_code == 200, priced.text

    cost = admin.get(f"{RUNS}/{run['id']}").json()["run"]["cost"]
    assert cost["currency"] == "USD"
    expected = (Decimal(30) * 3 + Decimal(10) * 15) / 1_000_000
    assert Decimal(cost["amount"]) == expected


def test_feedback_and_filters(roles, model, fake_llm):  # noqa: F811
    cid, run = run_one(roles, fake_llm)
    assert rate(
        roles.client("owner"), roles, cid, run["id"], rating="down", comment="Nope"
    ).is_success
    assert rate(roles.client("viewer"), roles, cid, run["id"], rating="up").is_success
    admin = roles.client("admin")

    detail = admin.get(f"{RUNS}/{run['id']}").json()
    assert {f["member_email"]: f["rating"] for f in detail["feedback"]} == {
        roles.user("owner").email: "down",
        roles.user("viewer").email: "up",
    }
    assert (detail["run"]["thumbs_up"], detail["run"]["thumbs_down"]) == (1, 1)

    def ids(**params):
        response = admin.get(RUNS, params=params)
        assert response.status_code == 200, response.text
        return [r["id"] for r in response.json()["items"]]

    assert ids() == [run["id"]]
    assert ids(workspace_id=roles.workspace_id) == [run["id"]]
    assert ids(workspace_id=str(uuid.uuid4())) == []
    assert ids(member_id=roles.user("owner").id) == [run["id"]]
    assert ids(member_id=roles.user("viewer").id) == []
    assert ids(status="completed") == [run["id"]]
    assert ids(status="failed") == []
    assert ids(started_from="2999-01-01T00:00:00Z") == []
    assert ids(started_to="2999-01-01T00:00:00Z") == [run["id"]]
    assert admin.get(RUNS, params={"status": "bogus"}).status_code == 422
    assert admin.get(f"{RUNS}/{uuid.uuid4()}").status_code == 404
