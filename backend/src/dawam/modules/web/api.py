"""``/api/v1/admin/web``: web search and web fetch for the assistant, admins only (ADR 0008).
No response ever carries an API key: it reports ``has_api_key``."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, User
from dawam.modules.workspaces import INSTALLATION, Action, can
from dawam.platform.errors import ApiError

from .service import API_KEY_MAX_LENGTH, WebProvider, WebService, WebSettings
from .tables import URL_MAX_LENGTH

router = APIRouter(prefix="/admin/web", tags=["web"])

ProviderKindName = Literal["searxng", "langsearch"]


def web_admin(user: CurrentUser) -> User:
    if not can(user, Action.MANAGE_SYSTEM_SETTINGS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


WebAdmin = Annotated[User, Depends(web_admin)]


def web_service(request: Request) -> WebService:
    state = request.app.state
    return WebService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=state.services.clock,
        network=getattr(state.services, "web_network", None),
    )


WebServiceDep = Annotated[WebService, Depends(web_service)]


class WebProviderOut(BaseModel):
    kind: ProviderKindName
    label: str
    internal: bool = Field(
        description="Runs inside the installation's network: the only kind internal-only "
        "Workspaces may use."
    )
    base_url: str
    has_api_key: bool
    test_ok: bool | None = Field(description="Null: not tested since it was saved.")
    test_error: str | None
    last_tested_at: datetime | None
    updated_at: datetime


class WebSettingsOut(BaseModel):
    enabled: bool = Field(description="Web search and web fetch for the assistant; off by default.")
    normal_provider: ProviderKindName | None = Field(
        description="The search provider of Workspaces that are not internal-only."
    )
    internal_provider: ProviderKindName | None = Field(
        description="The search provider of internal-only Workspaces (internal providers only)."
    )
    providers: list[WebProviderOut]
    updated_at: datetime | None


class WebSettingsIn(BaseModel):
    enabled: bool
    normal_provider: ProviderKindName | None = None
    internal_provider: ProviderKindName | None = Field(
        default=None,
        description="An internal provider only (`searxng`), else 422 `invalid_web_config`.",
    )


class WebProviderIn(BaseModel):
    base_url: str | None = Field(
        default=None,
        max_length=URL_MAX_LENGTH,
        description="SearXNG: the server's URL (JSON output must be on in its settings.yml).",
    )
    api_key: str | None = Field(
        default=None,
        max_length=API_KEY_MAX_LENGTH,
        description="LangSearch: the API key. Left out: the stored one is kept.",
    )


def _provider_out(view: WebProvider) -> WebProviderOut:
    return WebProviderOut(
        kind=view.kind,
        label=view.label,
        internal=view.internal,
        base_url=view.base_url,
        has_api_key=view.has_api_key,
        test_ok=view.test_ok,
        test_error=view.test_error,
        last_tested_at=view.last_tested_at,
        updated_at=view.updated_at,
    )


def _settings_out(view: WebSettings) -> WebSettingsOut:
    return WebSettingsOut(
        enabled=view.enabled,
        normal_provider=view.normal_provider,
        internal_provider=view.internal_provider,
        providers=[_provider_out(p) for p in view.providers],
        updated_at=view.updated_at,
    )


@router.get("", operation_id="getWebSettings")
def get_settings(_admin: WebAdmin, web: WebServiceDep) -> WebSettingsOut:
    """Whether the assistant may use the web, its search providers, and which provider each
    kind of Workspace uses (admins only)."""
    return _settings_out(web.settings())


@router.put("", operation_id="updateWebSettings")
def update_settings(body: WebSettingsIn, _admin: WebAdmin, web: WebServiceDep) -> WebSettingsOut:
    """Turn web access on or off and pick the providers (admins only). 422
    `invalid_web_config` for a provider that is not registered, or an external one for
    internal-only Workspaces."""
    return _settings_out(
        web.update_settings(
            enabled=body.enabled,
            normal_provider=body.normal_provider,
            internal_provider=body.internal_provider,
        )
    )


@router.put("/providers/{kind}", operation_id="saveWebProvider")
def save_provider(
    kind: ProviderKindName, body: WebProviderIn, _admin: WebAdmin, web: WebServiceDep
) -> WebSettingsOut:
    """Register or change a search provider (admins only). The key is sealed before it is
    stored. 422 `invalid_web_config` or `api_key_required`."""
    return _settings_out(web.save_provider(kind, base_url=body.base_url, api_key=body.api_key))


@router.delete("/providers/{kind}", operation_id="deleteWebProvider", status_code=204)
def delete_provider(kind: ProviderKindName, _admin: WebAdmin, web: WebServiceDep) -> Response:
    """Remove a search provider (admins only); Workspaces that used it have none until
    another is picked. 404 `web_provider_not_found`."""
    web.delete_provider(kind)
    return Response(status_code=204)


@router.post("/providers/{kind}/test", operation_id="testWebProvider")
def test_provider(kind: ProviderKindName, _admin: WebAdmin, web: WebServiceDep) -> WebProviderOut:
    """Run one search through the provider and keep the result (admins only)."""
    return _provider_out(web.test_provider(kind))
