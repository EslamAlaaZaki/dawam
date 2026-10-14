"""A Workspace's AI settings: model, internal-only and data-sharing level (spec stories 160,
161). Owners change them through the HTTP API; the gateway seam is
``RoleService.gateway_for_role`` / ``metered_gateway``; other modules ask
``WorkspaceAiService.policy``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI

from dawam.modules.audit import AuditService
from dawam.modules.llm import (
    DataSharingLevel,
    Message,
    ProviderService,
    RoleService,
    WorkspaceAiService,
)
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.errors import ApiError
from tests.llm.test_roles_budgets import BASE, assign, code
from tests.roles import RoleClients

ASK = [Message("user", "Hi")]


def register(client, name, *, internal, roles=("agent",), tested=True) -> dict:
    provider = client.post(
        f"{BASE}/providers",
        json={"name": f"P-{name}", "base_url": "http://llm.test/v1", "internal": internal},
    ).json()
    model = client.post(
        f"{BASE}/providers/{provider['id']}/models", json={"name": name, "roles": list(roles)}
    ).json()
    if tested:
        assert client.post(f"{BASE}/models/{model['id']}/test").json()["test_ok"] is True
    return model


def settings_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"


def body(**fields) -> dict:
    return {"internal_only": False, "data_sharing_level": "metadata", **fields}


def create_workspace(client, name="Fresh") -> str:
    response = client.post("/api/v1/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def read(client, workspace_id: str) -> dict:
    response = client.get(f"/api/v1/workspaces/{workspace_id}/ai-settings")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def gateways(app: FastAPI, clock, fake_llm):
    state = app.state
    providers = ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        adapters=state.services.llm_adapters,
    )
    workspaces = WorkspaceService(state.engine, clock=clock)
    return (
        RoleService(state.engine, providers=providers, clock=clock),
        WorkspaceAiService(state.engine, workspaces=workspaces, clock=clock),
    )


# -- defaults --------------------------------------------------------------------------


def test_a_new_workspace_is_internal_only_when_the_installation_has_an_internal_agent_model(
    admin_client, signed_in_client, fake_llm
):
    assert assign(admin_client, register(admin_client, "local", internal=True)).status_code == 200

    settings = read(signed_in_client, create_workspace(signed_in_client))

    assert settings["internal_only"] is True and settings["banner"] is False
    assert settings["data_sharing_level"] == "metadata" and settings["agent_model_id"] is None


@pytest.mark.parametrize("assigned", ["external", "nothing"])
def test_otherwise_a_new_workspace_is_not_internal_only_and_shows_a_banner(
    admin_client, signed_in_client, fake_llm, assigned
):
    if assigned == "external":
        agent = register(admin_client, "cloud", internal=False)
        assert assign(admin_client, agent).status_code == 200

    settings = read(signed_in_client, create_workspace(signed_in_client))

    assert settings["internal_only"] is False and settings["banner"] is True
    assert settings["internal_agent_available"] is False


def test_the_default_is_decided_when_the_workspace_is_created(
    admin_client, signed_in_client, fake_llm
):
    before = create_workspace(signed_in_client, "Before")
    assert assign(admin_client, register(admin_client, "local", internal=True)).status_code == 200
    after = create_workspace(signed_in_client, "After")

    assert read(signed_in_client, before)["internal_only"] is False
    assert read(signed_in_client, after)["internal_only"] is True


# -- choosing --------------------------------------------------------------------------


def test_an_owner_picks_the_agent_model_from_the_approved_list(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    big = register(admin, "big", internal=False)
    register(admin, "embed", internal=True, roles=("embedding",))
    register(admin, "untested", internal=True, tested=False)
    owner = roles.client("owner")

    listed = owner.get(settings_path(roles)).json()["approved_models"]
    chosen = owner.put(settings_path(roles), json=body(agent_model_id=big["id"]))

    assert [m["name"] for m in listed] == ["big"] and listed[0]["internal"] is False
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["agent_model_id"] == big["id"]
    assert chosen.json()["effective_agent_model_id"] == big["id"]
    assert read(owner, str(roles.workspace_id))["agent_model_id"] == big["id"]


def test_a_model_that_is_not_an_approved_agent_model_is_refused(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    embedding = register(admin, "embed", internal=True, roles=("embedding",))
    untested = register(admin, "untested", internal=True, tested=False)
    owner = roles.client("owner")

    refused = [
        owner.put(settings_path(roles), json=body(agent_model_id=model_id))
        for model_id in (embedding["id"], untested["id"], "00000000-0000-0000-0000-000000000000")
    ]

    assert [r.status_code for r in refused] == [422] * 3
    assert {code(r) for r in refused} == {"invalid_model_role"}


def test_internal_only_needs_an_internal_agent_model(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    cloud = register(admin, "cloud", internal=False)
    assert assign(admin, cloud).status_code == 200
    owner = roles.client("owner")

    refused = owner.put(settings_path(roles), json=body(internal_only=True))
    local = register(admin, "local", internal=True)
    allowed = owner.put(
        settings_path(roles), json=body(internal_only=True, agent_model_id=local["id"])
    )

    assert refused.status_code == 422 and code(refused) == "no_internal_model"
    assert allowed.status_code == 200 and allowed.json()["internal_only"] is True


def test_only_owners_change_the_settings_and_every_member_reads_them(roles: RoleClients, fake_llm):
    path = settings_path(roles)

    for role in ("viewer", "editor"):
        assert roles.client(role).get(path).status_code == 200
        assert roles.client(role).put(path, json=body()).status_code == 403
    assert roles.client("non_member").get(path).status_code == 404
    assert roles.client("non_member").put(path, json=body()).status_code == 404
    assert (
        roles.client("owner").put(path, json=body(data_sharing_level="samples")).status_code == 200
    )


def test_changes_are_audited(roles: RoleClients, app: FastAPI, fake_llm):
    owner = roles.client("owner")
    owner.put(settings_path(roles), json=body(data_sharing_level="documents"))
    owner.put(settings_path(roles), json=body(data_sharing_level="documents"))  # no change

    (entry,) = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="workspace_ai_settings", entity_id=str(roles.workspace_id)
    )
    feed = owner.get(f"/api/v1/workspaces/{roles.workspace_id}/activity").json()["items"]

    assert entry.old == {"data_sharing_level": "metadata"}
    assert entry.new == {"data_sharing_level": "documents"}
    assert entry.actor_id == roles.user("owner").id
    assert [i["verb"] for i in feed].count("workspace.ai_settings_changed") == 1


# -- the data-sharing policy -----------------------------------------------------------


def test_data_sharing_levels_are_cumulative(roles: RoleClients, gateways, fake_llm):
    _, ai = gateways
    expected = {
        "metadata": {"metadata"},
        "profiles": {"metadata", "profiles"},
        "documents": {"metadata", "profiles", "documents"},
        "samples": {"metadata", "profiles", "documents", "samples"},
    }

    for level, allowed in expected.items():
        roles.client("owner").put(settings_path(roles), json=body(data_sharing_level=level))
        policy = ai.policy(roles.workspace_id)
        assert {lv.value for lv in DataSharingLevel if policy.allows(lv)} == allowed, level


# -- the gateway refuses external providers for every role -----------------------------


@pytest.fixture
def internal_only_workspace(roles: RoleClients, fake_llm):
    """An internal-only Workspace, after which an admin moves every role to an external
    provider (or the provider is flagged external): the gateway is the last line."""
    admin = roles.client("admin")
    local = register(admin, "local", internal=True)
    assert assign(admin, local).status_code == 200
    assert roles.workspace_id  # created now: internal-only by default
    assert roles.client("owner").get(settings_path(roles)).json()["internal_only"] is True
    cloud_agent = register(admin, "cloud-agent", internal=False)
    cloud_light = register(admin, "cloud-light", internal=False)
    cloud_embed = register(admin, "cloud-embed", internal=False, roles=("embedding",))
    assert assign(admin, cloud_agent, cloud_light, cloud_embed).status_code == 200
    fake_llm.calls.clear()  # the connection tests above are not what is asserted
    return roles.workspace_id, cloud_agent, cloud_light, cloud_embed


def test_the_agent_role_is_refused_an_external_provider(
    internal_only_workspace, gateways, fake_llm
):
    workspace_id, *_ = internal_only_workspace
    gateway = gateways[0].gateway_for_role("agent", workspace_id=workspace_id)

    with pytest.raises(ApiError) as refused:
        list(gateway.chat(ASK))

    assert refused.value.code == "external_provider_refused"
    assert fake_llm.calls == []


def test_the_light_role_is_refused_an_external_provider(
    internal_only_workspace, gateways, fake_llm
):
    workspace_id, _, cloud_light, _ = internal_only_workspace
    roles_service = gateways[0]

    direct = roles_service.metered_gateway(
        uuid.UUID(cloud_light["id"]), "light", workspace_id=workspace_id
    )
    fallback = roles_service.gateway_for_role("light", workspace_id=workspace_id)

    for gateway in (direct, fallback):  # the fallback is the external agent model
        with pytest.raises(ApiError) as refused:
            list(gateway.chat(ASK))
        assert refused.value.code == "external_provider_refused"
    assert fake_llm.calls == []


def test_the_embedding_role_is_refused_an_external_provider(
    internal_only_workspace, gateways, fake_llm
):
    workspace_id, *_ = internal_only_workspace
    gateway = gateways[0].gateway_for_role("embedding", workspace_id=workspace_id)

    with pytest.raises(ApiError) as refused:
        gateway.embed(["text"])

    assert refused.value.code == "external_provider_refused"
    assert fake_llm.calls == []


def test_an_internal_only_workspace_uses_its_agent_model_when_the_light_model_is_external(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    local = register(admin, "local", internal=True)
    cloud_light = register(admin, "cloud-light", internal=False)
    assert assign(admin, local, cloud_light).status_code == 200
    assert roles.client("owner").get(settings_path(roles)).json()["internal_only"] is True

    gateway = gateways[0].gateway_for_role("light", workspace_id=roles.workspace_id)
    list(gateway.chat(ASK))

    assert gateway.role == "agent" and fake_llm.calls[-1].model == "local"


def test_a_workspace_that_is_not_internal_only_may_use_external_providers(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    cloud = register(admin, "cloud", internal=False)
    assert assign(admin, cloud).status_code == 200

    list(gateways[0].gateway_for_role("agent", workspace_id=roles.workspace_id).chat(ASK))

    assert fake_llm.calls[-1].model == "cloud"


def test_the_workspace_agent_model_is_the_one_called(roles: RoleClients, gateways, fake_llm):
    admin = roles.client("admin")
    assert assign(admin, register(admin, "default", internal=True)).status_code == 200
    own = register(admin, "own", internal=True)
    roles.client("owner").put(settings_path(roles), json=body(agent_model_id=own["id"]))

    list(gateways[0].gateway_for_role("agent", workspace_id=roles.workspace_id).chat(ASK))

    assert fake_llm.calls[-1].model == "own"


# -- Allowed Models -----------------------------------------------------------------------


def allowed_path(roles: RoleClients) -> str:
    return f"{BASE}/workspaces/{roles.workspace_id}/allowed-models"


def test_a_new_workspace_allows_every_registered_model(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    register(admin, "one", internal=False)
    register(admin, "two", internal=True)

    listed = admin.get(allowed_path(roles)).json()

    assert listed["model_ids"] == []
    assert sorted(m["name"] for m in listed["models"] if m["allowed"]) == ["one", "two"]


def test_an_admin_narrows_and_widens_the_allowed_models(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    one = register(admin, "one", internal=False)
    register(admin, "two", internal=False)

    narrowed = admin.put(allowed_path(roles), json={"model_ids": [one["id"]]})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["model_ids"] == [one["id"]]
    assert [m["name"] for m in narrowed.json()["models"] if m["allowed"]] == ["one"]
    approved = roles.client("owner").get(settings_path(roles)).json()["approved_models"]
    assert [m["name"] for m in approved] == ["one"]

    widened = admin.put(allowed_path(roles), json={"model_ids": []})
    assert widened.json()["model_ids"] == []
    assert len(widened.json()["models"]) == 2 and all(
        m["allowed"] for m in widened.json()["models"]
    )


def test_an_unknown_model_is_refused(roles: RoleClients, fake_llm):
    response = roles.client("admin").put(
        allowed_path(roles), json={"model_ids": [str(uuid.uuid4())]}
    )

    assert response.status_code == 422 and code(response) == "invalid_model"


def test_an_internal_only_workspace_is_never_offered_an_external_model(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    register(admin, "cloud", internal=False)
    local = register(admin, "local", internal=True)
    owner = roles.client("owner")
    assert owner.put(settings_path(roles), json=body(internal_only=True)).status_code == 200

    listed = admin.get(allowed_path(roles)).json()
    approved = owner.get(settings_path(roles)).json()["approved_models"]

    assert [m["name"] for m in listed["models"] if m["allowed"]] == ["local"]
    assert [m["id"] for m in approved] == [local["id"]]


def test_the_agent_model_override_must_be_an_allowed_model(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    one = register(admin, "one", internal=False)
    two = register(admin, "two", internal=False)
    admin.put(allowed_path(roles), json={"model_ids": [one["id"]]})
    owner = roles.client("owner")

    refused = owner.put(settings_path(roles), json=body(agent_model_id=two["id"]))
    accepted = owner.put(settings_path(roles), json=body(agent_model_id=one["id"]))

    assert refused.status_code == 422 and code(refused) == "model_not_allowed"
    assert accepted.status_code == 200


def test_a_model_outside_the_allowed_models_is_refused_at_call_time(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    default = register(admin, "default", internal=False)
    other = register(admin, "other", internal=False)
    assert assign(admin, default).status_code == 200
    admin.put(allowed_path(roles), json={"model_ids": [other["id"]]})
    fake_llm.calls.clear()

    with pytest.raises(ApiError) as refused:
        list(gateways[0].gateway_for_role("agent", workspace_id=roles.workspace_id).chat(ASK))

    assert refused.value.code == "model_not_allowed" and fake_llm.calls == []


def test_only_admins_read_or_change_the_allowed_models(roles: RoleClients, fake_llm):
    for role in ("owner", "editor", "viewer", "non_member"):
        client = roles.client(role)
        assert client.get(allowed_path(roles)).status_code == 403
        assert client.put(allowed_path(roles), json={"model_ids": []}).status_code == 403


# -- the Workspace's agent and light models ------------------------------------------------


def test_an_owner_sets_and_clears_the_agent_and_light_models(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    light = register(admin, "light-a", internal=False, roles=("light",))
    owner = roles.client("owner")

    chosen = owner.put(
        settings_path(roles), json=body(agent_model_id=agent["id"], light_model_id=light["id"])
    )
    cleared = owner.put(settings_path(roles), json=body())

    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["light_model_id"] == light["id"]
    assert chosen.json()["effective_light_model_id"] == light["id"]
    assert chosen.json()["effective_light_model_name"] == "light-a"
    assert [m["name"] for m in chosen.json()["light_models"]] == ["agent-a", "light-a"]
    assert cleared.json()["agent_model_id"] is None and cleared.json()["light_model_id"] is None


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_editors_and_viewers_cannot_choose_the_models(roles: RoleClients, fake_llm, role):
    light = register(roles.client("admin"), "light-a", internal=False)

    refused = roles.client(role).put(settings_path(roles), json=body(light_model_id=light["id"]))

    assert refused.status_code == 403


def test_the_light_model_must_be_an_allowed_chat_model(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    one = register(admin, "one", internal=False)
    two = register(admin, "two", internal=False)
    embed = register(admin, "embed", internal=False, roles=("embedding",))
    admin.put(allowed_path(roles), json={"model_ids": [one["id"], embed["id"]]})
    owner = roles.client("owner")

    outside = owner.put(settings_path(roles), json=body(light_model_id=two["id"]))
    embedding = owner.put(settings_path(roles), json=body(light_model_id=embed["id"]))
    inside = owner.put(settings_path(roles), json=body(light_model_id=one["id"]))

    assert outside.status_code == 422 and code(outside) == "model_not_allowed"
    assert embedding.status_code == 422 and code(embedding) == "invalid_model_role"
    assert inside.status_code == 200


def test_light_tasks_use_the_workspace_light_model_else_the_installations(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    default = register(admin, "default", internal=False)
    default_light = register(admin, "default-light", internal=False)
    own_light = register(admin, "own-light", internal=False)
    assert assign(admin, default, default_light).status_code == 200
    gateway_for = gateways[0].gateway_for_role

    list(gateway_for("light", workspace_id=roles.workspace_id).chat(ASK))
    fallback = fake_llm.calls[-1].model
    roles.client("owner").put(settings_path(roles), json=body(light_model_id=own_light["id"]))
    chosen = gateway_for("light", workspace_id=roles.workspace_id)
    list(chosen.chat(ASK))

    assert fallback == "default-light"
    assert fake_llm.calls[-1].model == "own-light" and chosen.role == "light"


def test_the_workspace_light_model_serves_light_tasks_without_an_installation_one(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    assert assign(admin, register(admin, "default", internal=False)).status_code == 200
    own_light = register(admin, "own-light", internal=False)
    roles.client("owner").put(settings_path(roles), json=body(light_model_id=own_light["id"]))

    list(gateways[0].gateway_for_role("light", workspace_id=roles.workspace_id).chat(ASK))

    assert fake_llm.calls[-1].model == "own-light"


def test_an_external_workspace_light_model_falls_back_to_the_agent_when_internal_only(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    local = register(admin, "local", internal=True)
    cloud = register(admin, "cloud", internal=False)
    assert assign(admin, local).status_code == 200
    owner = roles.client("owner")
    # A new Workspace here is internal-only already, which refuses an external model.
    assert owner.put(settings_path(roles), json=body()).status_code == 200
    chosen = owner.put(settings_path(roles), json=body(light_model_id=cloud["id"]))
    assert chosen.status_code == 200, chosen.text
    restricted = owner.put(
        settings_path(roles), json=body(internal_only=True, light_model_id=cloud["id"])
    )
    assert restricted.status_code == 200, restricted.text

    gateway = gateways[0].gateway_for_role("light", workspace_id=roles.workspace_id)
    list(gateway.chat(ASK))

    assert gateway.role == "agent" and fake_llm.calls[-1].model == "local"


# -- embedding model: chosen at creation, then fixed (ADR 0005) -------------------------


def create_with_embedding(client, model_id, name="Embeds"):
    return client.post("/api/v1/workspaces", json={"name": name, "embedding_model_id": model_id})


def test_a_new_workspace_takes_the_installations_embedding_model_by_default(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    embed = register(admin, "bge", internal=False, roles=("embedding",))
    assert assign(admin, agent, embedding=embed).status_code == 200

    offered = roles.client("owner").get("/api/v1/ai/embedding-models").json()
    workspace_id = create_workspace(roles.client("owner"))

    assert offered["default_model_id"] == embed["id"]
    assert [m["name"] for m in offered["models"]] == ["bge"]
    settings = read(roles.client("owner"), workspace_id)
    assert settings["embedding_model_id"] == embed["id"]
    assert settings["embedding_model_name"] == "bge"


def test_a_workspace_is_created_with_the_embedding_model_its_creator_picks(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    one = register(admin, "bge-small", internal=False, roles=("embedding",))
    two = register(admin, "bge-large", internal=False, roles=("embedding",))
    assert (
        assign(admin, register(admin, "agent-a", internal=False), embedding=one).status_code == 200
    )

    response = create_with_embedding(roles.client("owner"), two["id"])

    assert response.status_code == 201, response.text
    assert read(roles.client("owner"), response.json()["id"])["embedding_model_id"] == two["id"]


def test_the_creation_embedding_model_must_be_a_tested_embedding_model(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    chat = register(admin, "chat-a", internal=False)
    untested = register(admin, "bge-x", internal=False, roles=("embedding",), tested=False)
    owner = roles.client("owner")

    for model_id in (chat["id"], untested["id"], str(uuid.uuid4())):
        response = create_with_embedding(owner, model_id)
        assert response.status_code == 422, response.text
        assert code(response) == "invalid_model_role"
    names = [w["name"] for w in owner.get("/api/v1/workspaces").json()["items"]]
    assert "Embeds" not in names


def test_an_internal_only_workspace_is_not_created_with_an_external_embedding_model(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    internal_agent = register(admin, "agent-in", internal=True)
    external = register(admin, "bge-ext", internal=False, roles=("embedding",))
    internal = register(admin, "bge-in", internal=True, roles=("embedding",))
    assert assign(admin, internal_agent, embedding=internal).status_code == 200
    owner = roles.client("owner")

    refused = create_with_embedding(owner, external["id"])
    accepted = create_with_embedding(owner, internal["id"])
    offered = owner.get("/api/v1/ai/embedding-models").json()

    assert refused.status_code == 422 and code(refused) == "model_not_allowed"
    assert accepted.status_code == 201, accepted.text
    assert [m["name"] for m in offered["models"]] == ["bge-in"]


def test_the_embedding_model_cannot_be_changed_after_creation(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    one = register(admin, "bge-small", internal=False, roles=("embedding",))
    two = register(admin, "bge-large", internal=False, roles=("embedding",))
    owner = roles.client("owner")
    workspace_id = create_with_embedding(owner, one["id"]).json()["id"]
    path = f"/api/v1/workspaces/{workspace_id}/ai-settings"

    refused = owner.put(path, json=body(embedding_model_id=two["id"]))
    same = owner.put(path, json=body(embedding_model_id=one["id"]))
    left_out = owner.put(path, json=body())

    assert refused.status_code == 422 and code(refused) == "embedding_model_fixed"
    assert same.status_code == 200 and left_out.status_code == 200
    assert read(owner, workspace_id)["embedding_model_id"] == one["id"]


def test_changing_the_installations_embedding_model_leaves_existing_workspaces_alone(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    one = register(admin, "bge-small", internal=False, roles=("embedding",))
    two = register(admin, "bge-large", internal=False, roles=("embedding",))
    owner = roles.client("owner")
    assert assign(admin, agent, embedding=one).status_code == 200
    workspace_id = create_workspace(owner)

    assert assign(admin, agent, embedding=two).status_code == 200

    assert read(owner, workspace_id)["embedding_model_id"] == one["id"]
    assert read(owner, create_workspace(owner, "Newer"))["embedding_model_id"] == two["id"]


def test_an_internal_only_workspace_does_not_default_to_an_external_embedding_model(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    internal_agent = register(admin, "agent-in", internal=True)
    external = register(admin, "bge-ext", internal=False, roles=("embedding",))
    assert assign(admin, internal_agent, embedding=external).status_code == 200
    owner = roles.client("owner")

    settings = read(owner, create_workspace(owner))

    assert settings["internal_only"] is True
    assert settings["embedding_model_id"] is None
    assert owner.get("/api/v1/ai/embedding-models").json()["default_model_id"] is None
