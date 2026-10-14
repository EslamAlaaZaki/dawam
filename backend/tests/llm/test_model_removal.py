"""Removing a model in use (spec §6.18, ADR 0005): one replacement per role, applied to every
Workspace that has the model among its Workspace Models at once; an internal-only Workspace
needs an internal replacement, else its own; no re-index ever runs."""

from __future__ import annotations

from tests.llm.test_roles_budgets import BASE, code
from tests.llm.test_workspace_ai import body, create_workspace, default_of, read
from tests.llm.test_workspace_ai import register as register_model


def register(client, name, *, internal=False, **options):
    return register_model(client, name, internal=internal, **options)


def remove(client, model, **fields):
    return client.post(f"{BASE}/models/{model['id']}/removal", json=fields)


def set_models(client, workspace_id, *models, **fields):
    response = client.put(
        f"/api/v1/workspaces/{workspace_id}/ai-settings", json=body(*models, **fields)
    )
    assert response.status_code == 200, response.text


def agent_of(client, workspace_id) -> str | None:
    return default_of(read(client, workspace_id)["agent_models"])


def test_an_unused_model_is_removed_as_before(admin_client, fake_llm):
    spare = register(admin_client, "spare")

    assert admin_client.get(f"{BASE}/models/{spare['id']}/usage").json()["in_use"] is False
    assert admin_client.delete(f"{BASE}/models/{spare['id']}").status_code == 204


def test_deleting_a_model_in_use_is_refused_with_its_usage(
    admin_client, signed_in_client, fake_llm
):
    agent = register(admin_client, "main")
    create_workspace(signed_in_client, "First", agent=agent)

    assert admin_client.delete(f"{BASE}/models/{agent['id']}").status_code == 409
    usage = admin_client.get(f"{BASE}/models/{agent['id']}/usage").json()
    assert usage["in_use"] is True and usage["roles"] == ["agent"]
    assert [(w["name"], w["roles"]) for w in usage["workspaces"]] == [("First", ["agent"])]


def test_a_replacement_is_required_for_each_role_the_model_fills(
    admin_client, signed_in_client, fake_llm
):
    both = register(admin_client, "both", roles=("agent", "light"))
    workspace = create_workspace(signed_in_client, "First", agent=both, light=both)
    spare = register(admin_client, "spare", roles=("agent", "light"))

    partial = remove(admin_client, both, agent_model_id=spare["id"])

    assert partial.status_code == 422 and code(partial) == "replacement_required"
    assert partial.json()["error"]["details"]["roles"] == ["light"]
    done = remove(admin_client, both, agent_model_id=spare["id"], light_model_id=spare["id"])
    assert done.status_code == 204
    settings = read(signed_in_client, workspace)
    assert default_of(settings["agent_models"]) == "spare"
    assert default_of(settings["light_models"]) == "spare"


def test_every_affected_workspace_moves_to_the_replacement(
    admin_client, signed_in_client, fake_llm
):
    main = register(admin_client, "main")
    spare = register(admin_client, "spare")
    first = create_workspace(signed_in_client, "First", agent=main)
    second = create_workspace(signed_in_client, "Second", agent=spare)
    set_models(signed_in_client, second, [spare, main])

    usage = admin_client.get(f"{BASE}/models/{main['id']}/usage").json()
    assert sorted(w["name"] for w in usage["workspaces"]) == ["First", "Second"]
    assert remove(admin_client, main, agent_model_id=spare["id"]).status_code == 204

    assert agent_of(signed_in_client, first) == "spare"
    assert [m["name"] for m in read(signed_in_client, second)["agent_models"]] == ["spare"]


def test_a_bad_replacement_changes_nothing(admin_client, signed_in_client, fake_llm):
    main = register(admin_client, "main")
    untested = register(admin_client, "untested", tested=False)
    workspace = create_workspace(signed_in_client, "First", agent=main)

    assert remove(admin_client, main, agent_model_id=untested["id"]).status_code == 422
    assert remove(admin_client, main, agent_model_id=main["id"]).status_code == 422
    assert agent_of(signed_in_client, workspace) == "main"


def test_an_internal_only_workspace_needs_an_internal_replacement(
    admin_client, signed_in_client, fake_llm
):
    local = register(admin_client, "local", internal=True)
    other = register(admin_client, "other", internal=True)
    cloud = register(admin_client, "cloud", internal=False)
    private = create_workspace(signed_in_client, "Private", agent=local)
    open_ = create_workspace(signed_in_client, "Open", agent=local)
    set_models(signed_in_client, open_, local)

    refused = remove(admin_client, local, agent_model_id=cloud["id"])

    assert refused.status_code == 409 and code(refused) == "replacement_not_allowed"
    [reported] = refused.json()["error"]["details"]["workspaces"]
    assert reported["name"] == "Private" and reported["roles"] == ["agent"]
    assert agent_of(signed_in_client, open_) == "local"
    done = remove(
        admin_client,
        local,
        agent_model_id=cloud["id"],
        workspace_replacements=[{"workspace_id": private, "agent_model_id": other["id"]}],
    )
    assert done.status_code == 204, done.text
    assert agent_of(signed_in_client, private) == "other"
    assert agent_of(signed_in_client, open_) == "cloud"


def test_an_unindexed_embedding_model_is_replaced_like_any_other(
    admin_client, signed_in_client, fake_llm
):
    old = register(admin_client, "emb-old", roles=("embedding",))
    new = register(admin_client, "emb-new", roles=("embedding",))
    chat = register(admin_client, "chat")
    workspace = create_workspace(signed_in_client, "First", agent=chat, embedding=old)

    assert remove(admin_client, old, embedding_model_id=new["id"]).status_code == 204
    assert read(signed_in_client, workspace)["embedding_model"]["name"] == "emb-new"
