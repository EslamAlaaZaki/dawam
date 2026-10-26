# ruff: noqa: F811, F401
"""Requests per minute per member and per Workspace (AI enhancements §9): over a limit the
call fails with 429 ``ai_rate_limited``, and the counts reset every minute."""

from __future__ import annotations

from datetime import timedelta

import pytest

from dawam.modules.llm.role_service import MEMBER_REQUESTS_PER_MINUTE
from dawam.platform.errors import ApiError
from tests.llm.test_roles_budgets import ASK, role_service, setup
from tests.roles import RoleClients


def ask(role_service, workspace, user) -> None:
    list(role_service.gateway_for_role("agent", workspace_id=workspace, user_id=user).chat(ASK))


def test_a_member_over_the_limit_gets_a_clear_rate_limit_error(
    role_service, setup, roles: RoleClients
):
    user = roles.user("owner").id
    for _ in range(MEMBER_REQUESTS_PER_MINUTE):
        ask(role_service, setup, user)

    with pytest.raises(ApiError) as caught:
        ask(role_service, setup, user)

    assert caught.value.status_code == 429 and caught.value.code == "ai_rate_limited"
    assert caught.value.details["scope"] == "member"
    assert "Retry-After" in (caught.value.headers or {})


def test_the_limit_is_per_member(role_service, setup, roles: RoleClients):
    for _ in range(MEMBER_REQUESTS_PER_MINUTE):
        ask(role_service, setup, roles.user("owner").id)

    ask(role_service, setup, roles.user("admin").id)


def test_the_workspace_limit_applies_across_members(
    role_service, setup, roles: RoleClients, monkeypatch
):
    monkeypatch.setattr("dawam.modules.llm.role_service.WORKSPACE_REQUESTS_PER_MINUTE", 3)
    ask(role_service, setup, roles.user("owner").id)
    ask(role_service, setup, roles.user("admin").id)
    ask(role_service, setup, roles.user("owner").id)

    with pytest.raises(ApiError) as caught:
        ask(role_service, setup, roles.user("admin").id)

    assert caught.value.code == "ai_rate_limited" and caught.value.details["scope"] == "workspace"


def test_limits_reset_the_next_minute(role_service, setup, roles: RoleClients, clock):
    user = roles.user("owner").id
    for _ in range(MEMBER_REQUESTS_PER_MINUTE):
        ask(role_service, setup, user)
    clock.advance(timedelta(seconds=61))

    ask(role_service, setup, user)
