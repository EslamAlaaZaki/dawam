"""Removing a model or unticking a role (spec §6.18, ADR 0005): one rule. Only Workspaces where
the model is a role's default or only model need a replacement, picked per Workspace and
role; non-default extras are dropped silently; an internal-only Workspace takes only internal
models; no re-index ever runs."""

from __future__ import annotations

from tests.llm.test_roles_budgets import BASE, code
from tests.llm.test_workspace_ai import body, create_workspace, default_of, read
from tests.llm.test_workspace_ai import register as register_model


def register(client, name, *, internal=False, **options):
    return register_model(client, name, internal=internal, **options)


def remove(client, model, **fields):
    return client.post(f"{BASE}/models/{model['id']}/removal", json=fields)


def replace_in(workspace_id, **models):
    return {"workspace_id": workspace_id, **{f"{k}_model_id": m["id"] for k, m in models.items()}}


def untick(client, model, roles, *replacements):
    return client.put(
        f"{BASE}/models/{model['id']}/roles",
        json={"roles": roles, "workspace_replacements": list(replacements)},
    )


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
    assert usage["workspaces"][0]["internal_only"] is False


def test_a_replacement_is_required_per_workspace_and_role(admin_client, signed_in_client, fake_llm):
    both = register(admin_client, "both", roles=("agent", "light"))
    workspace = create_workspace(signed_in_client, "First", agent=both, light=both)
    spare = register(admin_client, "spare", roles=("agent", "light"))

    none = remove(admin_client, both)
    partial = remove(
        admin_client, both, workspace_replacements=[replace_in(workspace, agent=spare)]
    )

    assert none.status_code == 409 and code(none) == "replacement_required"
    assert partial.status_code == 409 and code(partial) == "replacement_required"
    [named] = partial.json()["error"]["details"]["workspaces"]
    assert named["name"] == "First" and named["roles"] == ["light"]
    done = remove(
        admin_client,
        both,
        workspace_replacements=[replace_in(workspace, agent=spare, light=spare)],
    )
    assert done.status_code == 204
    settings = read(signed_in_client, workspace)
    assert default_of(settings["agent_models"]) == "spare"
    assert default_of(settings["light_models"]) == "spare"


def test_only_workspaces_where_it_is_the_default_or_only_model_need_a_replacement(
    admin_client, signed_in_client, fake_llm
):
    main = register(admin_client, "main")
    spare = register(admin_client, "spare")
    extra = create_workspace(signed_in_client, "Extra", agent=spare)
    set_models(signed_in_client, extra, [spare, main])
    default = create_workspace(signed_in_client, "Default", agent=main)
    set_models(signed_in_client, default, [main, spare])

    usage = admin_client.get(f"{BASE}/models/{main['id']}/usage").json()

    assert [w["name"] for w in usage["workspaces"]] == ["Default"]
    done = remove(admin_client, main, workspace_replacements=[replace_in(default, agent=spare)])
    assert done.status_code == 204, done.text
    assert [m["name"] for m in read(signed_in_client, extra)["agent_models"]] == ["spare"]
    assert agent_of(signed_in_client, default) == "spare"


def test_a_model_held_only_as_an_extra_is_removed_at_once(admin_client, signed_in_client, fake_llm):
    main = register(admin_client, "main")
    extra = register(admin_client, "extra")
    workspace = create_workspace(signed_in_client, "First", agent=main)
    set_models(signed_in_client, workspace, [main, extra])

    assert admin_client.get(f"{BASE}/models/{extra['id']}/usage").json()["in_use"] is False
    assert admin_client.delete(f"{BASE}/models/{extra['id']}").status_code == 204
    assert [m["name"] for m in read(signed_in_client, workspace)["agent_models"]] == ["main"]


def test_the_replacement_becomes_the_default_even_when_already_an_extra(
    admin_client, signed_in_client, fake_llm
):
    main = register(admin_client, "main")
    spare = register(admin_client, "spare")
    workspace = create_workspace(signed_in_client, "First", agent=main)
    set_models(signed_in_client, workspace, [main, spare])

    done = remove(admin_client, main, workspace_replacements=[replace_in(workspace, agent=spare)])

    assert done.status_code == 204
    models = read(signed_in_client, workspace)["agent_models"]
    assert [(m["name"], m["is_default"]) for m in models] == [("spare", True)]


def test_a_bad_replacement_changes_nothing(admin_client, signed_in_client, fake_llm):
    main = register(admin_client, "main")
    untested = register(admin_client, "untested", tested=False)
    workspace = create_workspace(signed_in_client, "First", agent=main)

    for bad in (untested, main):
        refused = remove(
            admin_client, main, workspace_replacements=[replace_in(workspace, agent=bad)]
        )
        assert refused.status_code == 422
    assert agent_of(signed_in_client, workspace) == "main"


def test_an_internal_only_workspace_is_offered_and_accepts_only_internal_models(
    admin_client, signed_in_client, fake_llm
):
    local = register(admin_client, "local", internal=True)
    other = register(admin_client, "other", internal=True)
    cloud = register(admin_client, "cloud", internal=False)
    private = create_workspace(signed_in_client, "Private", agent=local)
    open_ = create_workspace(signed_in_client, "Open", agent=local)
    set_models(signed_in_client, open_, local)
    flagged = {
        w["name"]: w["internal_only"]
        for w in admin_client.get(f"{BASE}/models/{local['id']}/usage").json()["workspaces"]
    }
    assert flagged == {"Private": True, "Open": False}

    refused = remove(
        admin_client,
        local,
        workspace_replacements=[replace_in(private, agent=cloud), replace_in(open_, agent=cloud)],
    )

    assert refused.status_code == 409 and code(refused) == "replacement_not_allowed"
    [reported] = refused.json()["error"]["details"]["workspaces"]
    assert reported["name"] == "Private" and reported["roles"] == ["agent"]
    assert agent_of(signed_in_client, open_) == "local"
    done = remove(
        admin_client,
        local,
        workspace_replacements=[replace_in(private, agent=other), replace_in(open_, agent=cloud)],
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

    done = remove(admin_client, old, workspace_replacements=[replace_in(workspace, embedding=new)])

    assert done.status_code == 204
    assert read(signed_in_client, workspace)["embedding_model"]["name"] == "emb-new"


def test_unticking_a_role_shares_the_rule(admin_client, signed_in_client, fake_llm):
    both = register(admin_client, "both", roles=("agent", "light"))
    spare = register(admin_client, "spare", roles=("agent", "light"))
    chat = register(admin_client, "chat")
    needs = create_workspace(signed_in_client, "Needs", agent=chat, light=both)
    extra = create_workspace(signed_in_client, "Extra", agent=chat, light=spare)
    set_models(signed_in_client, extra, chat, [spare, both])

    refused = untick(admin_client, both, ["agent"])

    assert refused.status_code == 409 and code(refused) == "replacement_required"
    [named] = refused.json()["error"]["details"]["workspaces"]
    assert named["name"] == "Needs" and named["roles"] == ["light"]
    done = untick(admin_client, both, ["agent"], replace_in(needs, light=spare))
    assert done.status_code == 200, done.text
    assert done.json()["roles"] == ["agent"]
    assert default_of(read(signed_in_client, needs)["light_models"]) == "spare"
    assert [m["name"] for m in read(signed_in_client, extra)["light_models"]] == ["spare"]


def test_unticking_embedding_needs_a_replacement_too(admin_client, signed_in_client, fake_llm):
    old = register(admin_client, "emb-old", roles=("embedding",))
    new = register(admin_client, "emb-new", roles=("embedding",))
    chat = register(admin_client, "chat")
    workspace = create_workspace(signed_in_client, "First", agent=chat, embedding=old)

    refused = untick(admin_client, old, [])
    done = untick(admin_client, old, [], replace_in(workspace, embedding=new))

    assert refused.status_code == 409 and code(refused) == "replacement_required"
    assert done.status_code == 200 and done.json()["roles"] == []
    assert read(signed_in_client, workspace)["embedding_model"]["name"] == "emb-new"
