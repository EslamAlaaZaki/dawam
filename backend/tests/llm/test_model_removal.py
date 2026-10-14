"""Removing a model in use (spec §6.18, ADR 0005): one replacement per role, applied to the
installation and every affected Workspace at once; Workspaces the replacement is not
allowed for need their own; no re-index ever runs."""

from __future__ import annotations

from tests.llm.test_roles_budgets import BASE, assign, code
from tests.llm.test_workspace_ai import body, create_workspace, read
from tests.llm.test_workspace_ai import register as register_model


def register(client, name, *, internal=True, **options):
    return register_model(client, name, internal=internal, **options)


def remove(client, model, **fields):
    return client.post(f"{BASE}/models/{model['id']}/removal", json=fields)


def set_own_models(client, workspace_id, **fields):
    response = client.put(f"/api/v1/workspaces/{workspace_id}/ai-settings", json=body(**fields))
    assert response.status_code == 200, response.text


def test_an_unused_model_is_removed_as_before(admin_client, fake_llm):
    spare = register(admin_client, "spare")

    assert admin_client.get(f"{BASE}/models/{spare['id']}/usage").json()["in_use"] is False
    assert admin_client.delete(f"{BASE}/models/{spare['id']}").status_code == 204


def test_deleting_a_model_in_use_is_refused_with_its_usage(admin_client, fake_llm):
    agent = register(admin_client, "main")
    assert assign(admin_client, agent).status_code == 200

    assert admin_client.delete(f"{BASE}/models/{agent['id']}").status_code == 409
    usage = admin_client.get(f"{BASE}/models/{agent['id']}/usage").json()
    assert usage["in_use"] is True and usage["installation_roles"] == ["agent"]


def test_a_replacement_is_required_for_each_role_the_model_fills(admin_client, fake_llm):
    both = register(admin_client, "both", roles=("agent", "light"))
    assert assign(admin_client, both, light=both).status_code == 200
    spare = register(admin_client, "spare", roles=("agent", "light"))

    partial = remove(admin_client, both, agent_model_id=spare["id"])

    assert partial.status_code == 422 and code(partial) == "replacement_required"
    assert partial.json()["error"]["details"]["roles"] == ["light"]
    done = remove(admin_client, both, agent_model_id=spare["id"], light_model_id=spare["id"])
    assert done.status_code == 204
    assigned = admin_client.get(f"{BASE}/roles").json()
    assert assigned["agent_model_id"] == spare["id"] and assigned["light_model_id"] == spare["id"]


def test_every_affected_workspace_moves_to_the_replacement(
    admin_client, signed_in_client, fake_llm
):
    main = register(admin_client, "main")
    spare = register(admin_client, "spare")
    assert assign(admin_client, main).status_code == 200
    first = create_workspace(signed_in_client, "First")
    second = create_workspace(signed_in_client, "Second")
    for workspace in (first, second):
        set_own_models(signed_in_client, workspace, agent_model_id=main["id"])

    usage = admin_client.get(f"{BASE}/models/{main['id']}/usage").json()
    assert sorted(w["name"] for w in usage["workspaces"]) == ["First", "Second"]
    assert remove(admin_client, main, agent_model_id=spare["id"]).status_code == 204

    for workspace in (first, second):
        assert read(signed_in_client, workspace)["agent_model_id"] == spare["id"]
    assert admin_client.get(f"{BASE}/roles").json()["agent_model_id"] == spare["id"]


def test_a_bad_replacement_changes_nothing(admin_client, fake_llm):
    main = register(admin_client, "main")
    untested = register(admin_client, "untested", tested=False)
    assert assign(admin_client, main).status_code == 200

    assert remove(admin_client, main, agent_model_id=untested["id"]).status_code == 422
    assert remove(admin_client, main, agent_model_id=main["id"]).status_code == 422
    assert admin_client.get(f"{BASE}/roles").json()["agent_model_id"] == main["id"]


def test_workspaces_the_replacement_is_not_allowed_for_need_their_own(
    admin_client, signed_in_client, fake_llm
):
    main = register(admin_client, "main")
    spare = register(admin_client, "spare")
    other = register(admin_client, "other")
    assert assign(admin_client, main).status_code == 200
    narrow = create_workspace(signed_in_client, "Narrow")
    wide = create_workspace(signed_in_client, "Wide")
    for workspace in (narrow, wide):
        set_own_models(signed_in_client, workspace, agent_model_id=main["id"])
    listed = admin_client.put(
        f"{BASE}/workspaces/{narrow}/allowed-models",
        json={"model_ids": [main["id"], other["id"]]},
    )
    assert listed.status_code == 200, listed.text

    refused = remove(admin_client, main, agent_model_id=spare["id"])

    assert refused.status_code == 409 and code(refused) == "replacement_not_allowed"
    [reported] = refused.json()["error"]["details"]["workspaces"]
    assert reported["name"] == "Narrow" and reported["roles"] == ["agent"]
    assert read(signed_in_client, wide)["agent_model_id"] == main["id"]

    done = remove(
        admin_client,
        main,
        agent_model_id=spare["id"],
        workspace_replacements=[{"workspace_id": narrow, "agent_model_id": other["id"]}],
    )
    assert done.status_code == 204, done.text
    assert read(signed_in_client, narrow)["agent_model_id"] == other["id"]
    assert read(signed_in_client, wide)["agent_model_id"] == spare["id"]


def test_an_internal_only_workspace_needs_an_internal_replacement(
    admin_client, signed_in_client, fake_llm
):
    local = register(admin_client, "local", internal=True)
    cloud = register(admin_client, "cloud", internal=False)
    assert assign(admin_client, local).status_code == 200
    workspace = create_workspace(signed_in_client, "Private")
    set_own_models(signed_in_client, workspace, internal_only=True, agent_model_id=local["id"])

    refused = remove(admin_client, local, agent_model_id=cloud["id"])

    assert refused.status_code == 409 and code(refused) == "replacement_not_allowed"


def test_an_unindexed_embedding_model_is_replaced_like_any_other(admin_client, fake_llm):
    old = register(admin_client, "emb-old", roles=("embedding",))
    new = register(admin_client, "emb-new", roles=("embedding",))
    chat = register(admin_client, "chat")
    assert assign(admin_client, chat, embedding=old).status_code == 200

    assert remove(admin_client, old, embedding_model_id=new["id"]).status_code == 204
    assert admin_client.get(f"{BASE}/roles").json()["embedding_model_id"] == new["id"]
