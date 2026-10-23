"""Members' feedback on the assistant's answers through the HTTP API (ticket 27.3)."""

from __future__ import annotations

from typing import Any

from dawam.modules.llm import Reply

from .test_chat import ask, base, conversation, done, model  # noqa: F401  (model: fixture)


def answered(roles, fake_llm, text: str = "It is Net Revenue.") -> tuple[str, str]:
    """The owner asks one question in a shared conversation; returns (conversation, run)."""
    fake_llm.script(Reply(text=text))
    owner = roles.client("owner")
    cid = conversation(owner, roles, title="Feedback")
    owner.patch(f"{base(roles)}/conversations/{cid}", json={"shared_with_workspace": True})
    run = done(ask(owner, roles, cid, "What is Net Revenue?"))["run"]
    return cid, run["id"]


def rate(client, roles, cid: str, run_id: str, **body: Any):
    return client.put(f"{base(roles)}/conversations/{cid}/runs/{run_id}/feedback", json=body)


def test_a_member_rates_an_answer_once_and_changes_the_rating(roles, model, fake_llm):  # noqa: F811
    cid, run_id = answered(roles, fake_llm)
    owner = roles.client("owner")

    first = rate(owner, roles, cid, run_id, rating="down", comment="  Wrong KPI  ")
    assert first.status_code == 200, first.text
    assert first.json()["rating"] == "down" and first.json()["comment"] == "Wrong KPI"

    changed = rate(owner, roles, cid, run_id, rating="up")
    assert changed.status_code == 200 and changed.json()["comment"] is None

    detail = owner.get(f"{base(roles)}/conversations/{cid}").json()
    feedback = detail["messages"][0]["run"]["feedback"]
    assert feedback["rating"] == "up" and feedback["comment"] is None
    items = owner.get(f"{base(roles)}/feedback").json()["items"]
    assert [i["rating"] for i in items] == ["up"]  # still one rating, not two


def test_each_member_has_their_own_rating(roles, model, fake_llm):  # noqa: F811
    cid, run_id = answered(roles, fake_llm)
    owner, viewer = roles.client("owner"), roles.client("viewer")

    assert rate(viewer, roles, cid, run_id, rating="down", comment="Too vague").status_code == 200
    assert (
        owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"][0]["run"]["feedback"]
        is None
    )
    assert rate(owner, roles, cid, run_id, rating="up").status_code == 200

    items = owner.get(f"{base(roles)}/feedback").json()["items"]
    assert {i["member_email"]: i["rating"] for i in items} == {
        roles.user("owner").email: "up",
        roles.user("viewer").email: "down",
    }


def test_rating_is_refused_for_bad_input_unknown_runs_and_private_conversations(
    roles,
    model,  # noqa: F811
    fake_llm,
):
    cid, run_id = answered(roles, fake_llm)
    owner, editor = roles.client("owner"), roles.client("editor")
    owner.patch(f"{base(roles)}/conversations/{cid}", json={"shared_with_workspace": False})

    assert rate(owner, roles, cid, run_id, rating="meh").status_code == 422
    assert rate(owner, roles, cid, run_id, rating="up", comment="x" * 1001).status_code == 422
    unknown = "00000000-0000-4000-8000-000000000000"
    assert rate(owner, roles, cid, unknown, rating="up").status_code == 404
    assert rate(editor, roles, cid, run_id, rating="up").status_code == 404  # private
    assert roles.client("anonymous").put(
        f"{base(roles)}/conversations/{cid}/runs/{run_id}/feedback", json={"rating": "up"}
    ).status_code in (401, 403)


def test_owners_and_admins_see_feedback_and_export_eval_candidates(roles, model, fake_llm):  # noqa: F811
    cid, run_id = answered(roles, fake_llm)
    owner, editor = roles.client("owner"), roles.client("editor")
    assert (
        rate(owner, roles, cid, run_id, rating="down", comment="Missed the filter").status_code
        == 200
    )

    for who in ("owner", "admin"):
        listed = roles.client(who).get(f"{base(roles)}/feedback")
        assert listed.status_code == 200, who
        [item] = listed.json()["items"]
        assert item["question"] == "What is Net Revenue?"
        assert item["answer"] == "It is Net Revenue."
        assert item["comment"] == "Missed the filter"

    exported = owner.get(f"{base(roles)}/feedback/eval-candidates")
    assert exported.json()["cases"] == [
        {
            "id": f"feedback-{run_id}",
            "input": "What is Net Revenue?",
            "observed_answer": "It is Net Revenue.",
            "expected_tools": [],
            "label": "down",
            "note": "Missed the filter",
        }
    ]
    assert owner.get(f"{base(roles)}/feedback/eval-candidates?rating=up").json()["cases"] == []
    assert owner.get(f"{base(roles)}/feedback?rating=up").json()["items"] == []

    for who in ("editor", "viewer", "non_member"):
        assert roles.client(who).get(f"{base(roles)}/feedback").status_code in (403, 404), who
    assert editor.get(f"{base(roles)}/feedback/eval-candidates").status_code in (403, 404)
