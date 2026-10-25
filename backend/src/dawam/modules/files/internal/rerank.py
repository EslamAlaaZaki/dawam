"""The Workspace's rerank model, which reorders the top search results by relevance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import sqlalchemy as sa

from dawam.modules.llm import MeteredGateway, ProviderService, RoleService, workspace_model_id


@dataclass(frozen=True)
class RerankModel:
    is_internal: bool
    gateway: MeteredGateway
    """Metered: the token budgets are checked before every call, and internal-only applies."""


class RerankModels(Protocol):
    def current(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> RerankModel | None:
        """The Workspace's rerank model, or ``None`` if it has none usable."""
        ...


class NoRerankModels:
    """No Workspace has a rerank model: search keeps its fused order."""

    def current(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> RerankModel | None:
        return None


class RegisteredRerankModels:
    """The Workspace's own rerank model, one of its Workspace Models (optional)."""

    def __init__(self, providers: ProviderService, roles: RoleService, engine: sa.Engine) -> None:
        self._providers = providers
        self._roles = roles
        self._engine = engine

    def current(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> RerankModel | None:
        chosen = workspace_model_id(self._engine, workspace_id, "rerank")
        if chosen is None:
            return None
        for provider in self._providers.list_providers():
            for model in provider.models:
                if model.id == chosen and "rerank" in model.roles and model.test_ok:
                    gateway = self._roles.metered_gateway(
                        model.id, "rerank", workspace_id=workspace_id, user_id=user_id
                    )
                    return RerankModel(provider.is_internal, gateway)
        return None
