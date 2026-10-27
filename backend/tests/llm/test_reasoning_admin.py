"""The admin's thinking settings for a model, and the reasoning tokens recorded per call (#409)."""

from __future__ import annotations

import sqlalchemy as sa

from dawam.modules.llm import RoleService, Usage
from tests.llm.test_providers import BASE, add_model, add_provider


def test_an_admin_sets_a_models_thinking(admin_client):
    model = add_model(admin_client, add_provider(admin_client), roles=())
    assert model["reasoning"] == {
        "enabled": False,
        "effort": None,
        "budget_tokens": None,
        "show": False,
    }

    thinking = {"enabled": True, "effort": "high", "budget_tokens": 4000, "show": True}
    response = admin_client.put(
        f"{BASE}/models/{model['id']}", json={"context_window": 65536, "reasoning": thinking}
    )

    assert response.status_code == 200, response.text
    assert response.json()["reasoning"] == thinking
    # A later update that leaves "reasoning" out keeps it.
    kept = admin_client.put(f"{BASE}/models/{model['id']}", json={"context_window": 32768})
    assert kept.json()["reasoning"] == thinking
    bad = admin_client.put(
        f"{BASE}/models/{model['id']}", json={"reasoning": {"enabled": True, "effort": "max"}}
    )
    assert bad.status_code == 422


def test_reasoning_tokens_are_recorded_with_the_call(app):
    roles = RoleService(app.state.engine, providers=None, clock=app.state.services.clock)  # type: ignore[arg-type]
    roles.record_usage(
        Usage(10, 300, reasoning_tokens=256),
        role="agent",
        model_name="o4-mini",
        workspace_id=None,
        user_id=None,
    )

    with app.state.engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT completion_tokens, reasoning_tokens FROM llm_usage")
        ).one()
    assert tuple(row) == (300, 256)
