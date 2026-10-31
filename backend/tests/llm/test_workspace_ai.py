"""A Workspace's AI settings: its Workspace Models, internal-only and data-sharing level (spec
§6.18, stories 160, 161). Owners change them through the HTTP API; the gateway seam is
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
from tests.files.test_files import system, upload
from tests.files.test_search import Policy
from tests.llm.test_roles_budgets import BASE, add_model, code
from tests.roles import RoleClients

ASK = [Message("user", "Hi")]
NEW = "/api/v1/ai/new-workspace-models"


def register(client, name, *, internal, roles=("agent",), tested=True) -> dict:
    provider = client.post(
        f"{BASE}/providers",
        json={"name": f"P-{name}", "base_url": "http://llm.test/v1", "internal": internal},
    ).json()
    model = add_model(client, provider, name, roles)
    if tested:
        assert client.post(f"{BASE}/models/{model['id']}/test").json()["test_ok"] is True
    return model


def settings_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"


def body(agent=None, light=None, embedding=None, **fields) -> dict:
    """A settings request: ``agent`` / ``light`` are one model or a list (the first is the
    default)."""

    def ids(models) -> list[str]:
        if models is None:
            return []
        return [m["id"] for m in (models if isinstance(models, list) else [models])]

    agents, lights = ids(agent), ids(light)
    return {
        "internal_only": False,
        "data_sharing_level": "metadata",
        "agent_model_ids": agents,
        "default_agent_model_id": agents[0] if agents else None,
        "light_model_ids": lights,
        "default_light_model_id": lights[0] if lights else None,
        "embedding_model_id": embedding["id"] if embedding else None,
        **fields,
    }


def create(client, name="Fresh", **models):
    return client.post(
        "/api/v1/workspaces",
        json={"name": name, **{f"{role}_model_id": m["id"] for role, m in models.items()}},
    )


def create_workspace(client, name="Fresh", **models) -> str:
    response = create(client, name, **models)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def read(client, workspace_id) -> dict:
    response = client.get(f"/api/v1/workspaces/{workspace_id}/ai-settings")
    assert response.status_code == 200, response.text
    return response.json()


def names(models: list[dict]) -> list[str]:
    return [m["name"] for m in models]


def default_of(models: list[dict]) -> str | None:
    return next((m["name"] for m in models if m["is_default"]), None)


@pytest.fixture
def indexing(services, fake_llm):
    """Documents get embedded: the files module's AI policy lets them (set before the app
    is built)."""
    services.document_ai = Policy()


@pytest.fixture(autouse=True)
def workspace_first(request):
    """The roles' Workspace exists before any model is registered, as an installation's
    first Workspaces do: it was created with no model to choose."""
    if "indexing" in request.fixturenames:
        request.getfixturevalue("indexing")
    if "roles" in request.fixturenames:
        assert request.getfixturevalue("roles").workspace_id


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
    local = register(admin_client, "local", internal=True)

    settings = read(signed_in_client, create_workspace(signed_in_client, agent=local))

    assert settings["internal_only"] is True and settings["banner"] is False
    assert settings["data_sharing_level"] == "metadata"


@pytest.mark.parametrize("registered", ["external", "nothing"])
def test_otherwise_a_new_workspace_is_not_internal_only_and_shows_a_banner(
    admin_client, signed_in_client, fake_llm, registered
):
    models = {}
    if registered == "external":
        models["agent"] = register(admin_client, "cloud", internal=False)

    settings = read(signed_in_client, create_workspace(signed_in_client, **models))

    assert settings["internal_only"] is False and settings["banner"] is True
    assert settings["internal_agent_available"] is False


def test_the_default_is_decided_when_the_workspace_is_created(
    admin_client, signed_in_client, fake_llm
):
    before = create_workspace(signed_in_client, "Before")
    local = register(admin_client, "local", internal=True)
    after = create_workspace(signed_in_client, "After", agent=local)

    assert read(signed_in_client, before)["internal_only"] is False
    assert read(signed_in_client, after)["internal_only"] is True


# -- creation: the creator chooses the Workspace Models ----------------------------------


def test_a_workspace_is_created_with_its_embedding_default_agent_and_light_models(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    light = register(admin, "light-a", internal=False, roles=("light",))
    embed = register(admin, "bge", internal=False, roles=("embedding",))
    owner = roles.client("owner")

    offered = owner.get(NEW).json()
    settings = read(owner, create_workspace(owner, agent=agent, light=light, embedding=embed))

    assert offered["internal_only"] is False
    assert [names(offered["choices"][r]) for r in ("agent", "light", "embedding")] == [
        ["agent-a"],
        ["light-a"],
        ["bge"],
    ]
    assert default_of(settings["agent_models"]) == "agent-a"
    assert default_of(settings["light_models"]) == "light-a"
    assert settings["embedding_model"]["name"] == "bge"


def test_the_light_model_is_optional_at_creation(roles: RoleClients, fake_llm):
    agent = register(roles.client("admin"), "agent-a", internal=False)

    settings = read(roles.client("owner"), create_workspace(roles.client("owner"), agent=agent))

    assert settings["light_models"] == [] and settings["embedding_model"] is None


def test_the_default_agent_model_is_required_when_there_is_one_to_choose(
    roles: RoleClients, fake_llm
):
    register(roles.client("admin"), "agent-a", internal=False)

    response = create(roles.client("owner"))

    assert response.status_code == 422 and code(response) == "agent_model_required"
    assert response.json()["error"]["details"]["field"] == "agent_model_id"


def test_the_embedding_model_is_optional_only_when_there_is_none_to_choose(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    owner = roles.client("owner")
    without = create(owner, "Without", agent=agent)
    register(admin, "bge", internal=False, roles=("embedding",))

    refused = create(owner, "Refused", agent=agent)

    assert without.status_code == 201, without.text
    assert refused.status_code == 422 and code(refused) == "embedding_model_required"


def test_creation_refuses_a_model_not_enabled_for_its_role(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    embed = register(admin, "bge", internal=False, roles=("embedding",))
    untested = register(admin, "fresh", internal=False, tested=False)
    owner = roles.client("owner")

    refused = [
        create(owner, agent=embed, embedding=embed),
        create(owner, agent=untested, embedding=embed),
        create(owner, agent={"id": str(uuid.uuid4())}, embedding=embed),
        create(owner, agent=agent, embedding=agent),
        create(owner, agent=agent, light=agent, embedding=embed),
    ]

    assert [r.status_code for r in refused] == [422] * 5
    assert {code(r) for r in refused} == {"invalid_model_role"}
    assert "Fresh" not in names(owner.get("/api/v1/workspaces").json()["items"])


def test_an_internal_only_workspace_is_offered_and_created_with_internal_models_only(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    local = register(admin, "agent-in", internal=True)
    cloud = register(admin, "agent-ext", internal=False)
    external = register(admin, "bge-ext", internal=False, roles=("embedding",))
    internal = register(admin, "bge-in", internal=True, roles=("embedding",))
    owner = roles.client("owner")

    offered = owner.get(NEW).json()
    refused = [
        create(owner, agent=cloud, embedding=internal),
        create(owner, agent=local, embedding=external),
    ]
    accepted = create(owner, agent=local, embedding=internal)

    assert offered["internal_only"] is True
    assert names(offered["choices"]["agent"]) == ["agent-in"]
    assert names(offered["choices"]["embedding"]) == ["bge-in"]
    assert {(r.status_code, code(r)) for r in refused} == {(422, "model_not_allowed")}
    assert accepted.status_code == 201, accepted.text


# -- choosing the Workspace Models --------------------------------------------------------


def test_an_owner_chooses_several_agent_and_light_models_with_a_default_each(
    roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    big = register(admin, "big", internal=False)
    small = register(admin, "small", internal=False, roles=("agent", "light"))
    tiny = register(admin, "tiny", internal=False, roles=("light",))
    register(admin, "untested", internal=False, tested=False)
    owner = roles.client("owner")

    chosen = owner.put(
        settings_path(roles),
        json=body([big, small], [tiny, small], default_light_model_id=small["id"]),
    )

    assert chosen.status_code == 200, chosen.text
    settings = read(owner, roles.workspace_id)
    assert names(settings["agent_models"]) == ["big", "small"]
    assert default_of(settings["agent_models"]) == "big"
    assert names(settings["light_models"]) == ["small", "tiny"]
    assert default_of(settings["light_models"]) == "small"
    assert names(settings["choices"]["agent"]) == ["big", "small"]
    assert names(settings["choices"]["light"]) == ["small", "tiny"]


def test_exactly_one_default_agent_model_and_one_default_light_model(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    one = register(admin, "one", internal=False, roles=("agent", "light"))
    two = register(admin, "two", internal=False, roles=("agent", "light"))
    owner = roles.client("owner")

    refused = {
        "no agent": owner.put(settings_path(roles), json=body()),
        "no default agent": owner.put(
            settings_path(roles), json=body([one, two], default_agent_model_id=None)
        ),
        "default not chosen": owner.put(
            settings_path(roles), json=body(one, default_agent_model_id=two["id"])
        ),
        "no default light": owner.put(
            settings_path(roles), json=body(one, [one, two], default_light_model_id=None)
        ),
    }
    alone = owner.put(settings_path(roles), json=body(one, default_agent_model_id=None))

    assert {k: (r.status_code, code(r)) for k, r in refused.items()} == {
        "no agent": (422, "agent_model_required"),
        "no default agent": (422, "invalid_default_model"),
        "default not chosen": (422, "invalid_default_model"),
        "no default light": (422, "invalid_default_model"),
    }
    assert alone.status_code == 200 and default_of(alone.json()["agent_models"]) == "one"


def test_a_model_not_enabled_for_the_role_is_refused(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    embedding = register(admin, "embed", internal=False, roles=("embedding",))
    untested = register(admin, "untested", internal=False, tested=False)
    owner = roles.client("owner")

    refused = [
        owner.put(settings_path(roles), json=body(m))
        for m in (embedding, untested, {"id": "00000000-0000-0000-0000-000000000000"})
    ] + [
        owner.put(settings_path(roles), json=body(agent, light=agent)),
        owner.put(settings_path(roles), json=body(agent, embedding=agent)),
    ]

    assert [r.status_code for r in refused] == [422] * 5
    assert {code(r) for r in refused} == {"invalid_model_role"}


def test_internal_only_takes_only_internal_models(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    cloud = register(admin, "cloud", internal=False)
    owner = roles.client("owner")

    no_internal = owner.put(settings_path(roles), json=body(cloud, internal_only=True))
    local = register(admin, "local", internal=True)
    external = owner.put(settings_path(roles), json=body(cloud, internal_only=True))
    accepted = owner.put(settings_path(roles), json=body(local, internal_only=True))

    assert no_internal.status_code == 422 and code(no_internal) == "no_internal_model"
    assert external.status_code == 422 and code(external) == "model_not_allowed"
    assert accepted.status_code == 200 and accepted.json()["internal_only"] is True


def test_the_embedding_model_changes_after_indexing_and_the_documents_are_embedded_again(
    indexing, roles: RoleClients, fake_llm
):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    one = register(admin, "bge-small", internal=False, roles=("embedding",))
    two = register(admin, "bge-large", internal=False, roles=("embedding",))
    owner = roles.client("owner")
    sharing = {"data_sharing_level": "documents"}

    set_one = owner.put(settings_path(roles), json=body(agent, embedding=one, **sharing))
    assert upload(roles, system(roles), "sad.md", b"Postings are booked daily.").is_success
    changed = owner.put(settings_path(roles), json=body(agent, embedding=two, **sharing))
    jobs = owner.get(f"/api/v1/workspaces/{roles.workspace_id}/jobs").json()["items"]

    assert set_one.status_code == 200 and set_one.json()["embedding_rebuilding"] is False
    assert changed.status_code == 200, changed.text
    assert changed.json()["embedding_model"]["name"] == "bge-large"
    assert changed.json()["embedding_rebuilding"] is True
    # The inline runner rebuilt the vectors once the change committed, and switched to them.
    assert read(owner, roles.workspace_id)["embedding_rebuilding"] is False
    assert [j["title"] for j in jobs if j["type"] == "reindex_documents"] == ["Re-embed documents"]


def test_model_fields_left_out_keep_their_value(roles: RoleClients, fake_llm):
    admin = roles.client("admin")
    agent = register(admin, "agent-a", internal=False)
    light = register(admin, "light-a", internal=False, roles=("light",))
    bge = register(admin, "bge", internal=False, roles=("embedding",))
    owner = roles.client("owner")
    owner.put(settings_path(roles), json=body(agent, light, embedding=bge))

    changed = owner.put(
        settings_path(roles), json={"internal_only": False, "data_sharing_level": "samples"}
    )

    assert changed.status_code == 200, changed.text
    assert default_of(changed.json()["agent_models"]) == "agent-a"
    assert default_of(changed.json()["light_models"]) == "light-a"
    assert changed.json()["embedding_model"]["name"] == "bge"


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


def test_an_archived_workspaces_settings_are_read_only(roles: RoleClients, fake_llm):
    owner = roles.client("owner")
    archived = owner.post(f"/api/v1/workspaces/{roles.workspace_id}/archive")
    assert archived.is_success, archived.text

    assert owner.get(settings_path(roles)).status_code == 200
    assert owner.put(settings_path(roles), json=body()).status_code in (403, 409)


def test_changes_are_audited(roles: RoleClients, app: FastAPI, fake_llm):
    agent = register(roles.client("admin"), "agent-a", internal=False)
    owner = roles.client("owner")
    owner.put(settings_path(roles), json=body(agent, data_sharing_level="documents"))
    owner.put(settings_path(roles), json=body(agent, data_sharing_level="documents"))  # same

    (entry,) = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="workspace_ai_settings", entity_id=str(roles.workspace_id)
    )
    feed = owner.get(f"/api/v1/workspaces/{roles.workspace_id}/activity").json()["items"]

    assert entry.old == {
        "agent_model_ids": [],
        "data_sharing_level": "metadata",
        "default_agent_model_id": None,
    }
    assert entry.new == {
        "agent_model_ids": [agent["id"]],
        "data_sharing_level": "documents",
        "default_agent_model_id": agent["id"],
    }
    assert entry.actor_id == roles.user("owner").id
    assert [i["verb"] for i in feed].count("workspace.ai_settings_changed") == 1


def test_switching_internal_only_is_audited(roles: RoleClients, app: FastAPI, fake_llm):
    local = register(roles.client("admin"), "agent-local", internal=True)
    owner = roles.client("owner")
    owner.put(settings_path(roles), json=body(local, internal_only=False))
    owner.put(settings_path(roles), json=body(local, internal_only=True))

    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="workspace_ai_settings", entity_id=str(roles.workspace_id)
    )

    assert any(
        e.old.get("internal_only") is False and e.new.get("internal_only") is True for e in entries
    )


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


# -- the gateway resolves each call's model from the Workspace ----------------------------


def test_agent_light_and_embedding_calls_use_the_workspaces_defaults(
    roles: RoleClients, gateways, fake_llm
):
    admin = roles.client("admin")
    big = register(admin, "big", internal=False)
    other = register(admin, "other", internal=False)
    small = register(admin, "small", internal=False, roles=("light",))
    bge = register(admin, "bge", internal=False, roles=("embedding",))
    chosen = body([big, other], small, embedding=bge)
    assert roles.client("owner").put(settings_path(roles), json=chosen).status_code == 200
    gateway_for = gateways[0].gateway_for_role

    agent = gateway_for("agent", workspace_id=roles.workspace_id)
    list(agent.chat(ASK))
    agent_model = fake_llm.calls[-1].model
    light = gateway_for("light", workspace_id=roles.workspace_id)
    list(light.chat(ASK))
    light_model = fake_llm.calls[-1].model
    gateway_for("embedding", workspace_id=roles.workspace_id).embed(["text"])

    assert (agent_model, agent.role) == ("big", "agent")
    assert (light_model, light.role) == ("small", "light")
    assert fake_llm.calls[-1].model == "bge"


def test_light_calls_fall_back_to_the_default_agent_model(roles: RoleClients, gateways, fake_llm):
    big = register(roles.client("admin"), "big", internal=False)
    roles.client("owner").put(settings_path(roles), json=body(big))

    gateway = gateways[0].gateway_for_role("light", workspace_id=roles.workspace_id)
    list(gateway.chat(ASK))

    assert gateway.role == "agent" and fake_llm.calls[-1].model == "big"


def test_a_workspace_without_an_embedding_model_has_none_to_call(
    roles: RoleClients, gateways, fake_llm
):
    big = register(roles.client("admin"), "big", internal=False)
    register(roles.client("admin"), "bge", internal=False, roles=("embedding",))
    roles.client("owner").put(settings_path(roles), json=body(big))

    with pytest.raises(ApiError) as refused:
        gateways[0].gateway_for_role("embedding", workspace_id=roles.workspace_id)

    assert refused.value.code == "model_role_unassigned"


@pytest.fixture
def internal_only_workspace(roles: RoleClients, fake_llm):
    """An internal-only Workspace whose models' provider an admin then flags external: the
    gateway is the last line."""
    admin = roles.client("admin")
    provider = admin.post(
        f"{BASE}/providers",
        json={"name": "Local", "base_url": "http://llm.test/v1", "internal": True},
    ).json()
    models = {
        role: add_model(admin, provider, f"local-{role}", (role,))
        for role in ("agent", "light", "embedding")
    }
    for model in models.values():
        assert admin.post(f"{BASE}/models/{model['id']}/test").json()["test_ok"] is True
    chosen = body(
        models["agent"], models["light"], embedding=models["embedding"], internal_only=True
    )
    assert roles.client("owner").put(settings_path(roles), json=chosen).status_code == 200
    flagged = admin.put(
        f"{BASE}/providers/{provider['id']}",
        json={"name": "Local", "base_url": "http://llm.test/v1", "internal": False},
    )
    assert flagged.status_code == 200, flagged.text
    fake_llm.calls.clear()  # the connection tests above are not what is asserted
    return roles.workspace_id


@pytest.mark.parametrize("role", ["agent", "light", "embedding"])
def test_every_role_is_refused_an_external_provider(
    internal_only_workspace, gateways, fake_llm, role
):
    gateway = gateways[0].gateway_for_role(role, workspace_id=internal_only_workspace)

    with pytest.raises(ApiError) as refused:
        if role == "embedding":
            gateway.embed(["text"])
        else:
            list(gateway.chat(ASK))

    assert refused.value.code == "external_provider_refused"
    assert fake_llm.calls == []


def test_a_workspace_that_is_not_internal_only_may_use_external_providers(
    roles: RoleClients, gateways, fake_llm
):
    cloud = register(roles.client("admin"), "cloud", internal=False)
    roles.client("owner").put(settings_path(roles), json=body(cloud))

    list(gateways[0].gateway_for_role("agent", workspace_id=roles.workspace_id).chat(ASK))

    assert fake_llm.calls[-1].model == "cloud"


def test_allowed_models_are_gone(roles: RoleClients, fake_llm):
    path = f"{BASE}/workspaces/{roles.workspace_id}/allowed-models"

    assert roles.client("admin").get(path).status_code == 404
