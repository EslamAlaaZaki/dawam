"""Outbound web access for the assistant: the admin's settings and search providers, and
the searches and fetches themselves (ADR 0008).

Authorization is the caller's job: the admin router restricts the settings to admins, and
the assistant's tools run as the member through ``BoundTools``. Off by default (no
settings row). Which search provider a Workspace uses follows its internal-only flag
(``internal_only``, a port the composition root fills from the llm module): an
internal-only Workspace only ever uses an internal provider (SearXNG), whatever the admin
picked for the others. Every query and every fetch is audited (channel ``ai``) before it
leaves. API keys are sealed with ``SecretBox`` and never returned: callers get
``has_api_key``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_text

from .internal.fetch import FetchedPage, FetchError, fetch_page
from .internal.network import Network, SystemNetwork
from .internal.providers import (
    KINDS,
    LANGSEARCH_URL,
    LangSearch,
    SearchError,
    SearchProvider,
    SearchResult,
    Searxng,
)
from .internal.strip import strip_values
from .tables import URL_MAX_LENGTH, WebProviderRecord, WebSettingsRecord

API_KEY_CONTEXT = "web.search_provider.api_key"
API_KEY_MAX_LENGTH = 1000
MAX_RESULTS = 10
TEST_QUERY = "data warehouse star schema"
AUDIT_SEARCH = "web_search"
AUDIT_FETCH = "web_fetch"
MAX_AUDIT_URL_CHARS = 500


@dataclass(frozen=True)
class WebProvider:
    kind: str
    label: str
    internal: bool
    base_url: str
    has_api_key: bool
    test_ok: bool | None
    test_error: str | None
    last_tested_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class WebSettings:
    enabled: bool
    normal_provider: str | None
    internal_provider: str | None
    providers: list[WebProvider]
    updated_at: datetime | None


@dataclass(frozen=True)
class WebSearch:
    provider: str
    query: str
    """The query as it was sent, values stripped."""
    results: list[SearchResult]


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_web_config", message, {"field": field})


def _unknown_kind(kind: str) -> ApiError:
    return ApiError(404, "web_provider_not_found", f"There is no search provider `{kind}`.")


def _clean_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        raise _invalid("The URL must not be empty.", "base_url")
    if len(url) > URL_MAX_LENGTH:
        raise _invalid(f"The URL must be at most {URL_MAX_LENGTH} characters.", "base_url")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise _invalid("The URL must start with http:// or https://.", "base_url")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _invalid("The URL must not hold credentials, a query or a fragment.", "base_url")
    return url


class WebService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        encryption_key: bytes,
        clock: Clock,
        network: Network | None = None,
        internal_only: Callable[[uuid.UUID], bool] = lambda _workspace_id: True,
    ) -> None:
        self._engine = engine
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._network = network or SystemNetwork()
        self._internal_only = internal_only

    # -- admin -------------------------------------------------------------------------

    def settings(self) -> WebSettings:
        with Session(self._engine) as db:
            return self._settings(db)

    def update_settings(
        self, *, enabled: bool, normal_provider: str | None, internal_provider: str | None
    ) -> WebSettings:
        with Session(self._engine) as db, db.begin():
            registered = {r.kind for r in db.scalars(sa.select(WebProviderRecord))}
            if normal_provider is not None and normal_provider not in registered:
                raise _invalid(
                    "Register the provider before choosing it for Workspaces.", "normal_provider"
                )
            if internal_provider is not None:
                if internal_provider not in KINDS or not KINDS[internal_provider].internal:
                    raise _invalid(
                        "Internal-only Workspaces can use an internal provider (SearXNG) only.",
                        "internal_provider",
                    )
                if internal_provider not in registered:
                    raise _invalid(
                        "Register the provider before choosing it for Workspaces.",
                        "internal_provider",
                    )
            row = db.get(WebSettingsRecord, 1)
            now = self._clock()
            if row is None:
                row = WebSettingsRecord(id=1, enabled=enabled, updated_at=now)
                db.add(row)
            row.enabled = enabled
            row.normal_provider = normal_provider
            row.internal_provider = internal_provider
            row.updated_at = now
            db.flush()
            return self._settings(db)

    def save_provider(
        self, kind: str, *, base_url: str | None = None, api_key: str | None = None
    ) -> WebSettings:
        """Register or change a provider. SearXNG takes its URL; LangSearch its API key
        (``None`` keeps the stored one)."""
        if kind not in KINDS:
            raise _unknown_kind(kind)
        with Session(self._engine) as db, db.begin():
            record = db.get(WebProviderRecord, kind)
            now = self._clock()
            if kind == "searxng":
                url = _clean_url(base_url or "")
                secret = None
            else:
                url = LANGSEARCH_URL
                if api_key is not None and len(api_key.strip()) > API_KEY_MAX_LENGTH:
                    raise _invalid(
                        f"The API key must be at most {API_KEY_MAX_LENGTH} characters.", "api_key"
                    )
                if api_key is not None and api_key.strip():
                    secret = self._seal(api_key.strip())
                elif record is not None and record.secret_encrypted and api_key is None:
                    secret = record.secret_encrypted
                else:
                    raise ApiError(
                        422,
                        "api_key_required",
                        "LangSearch needs an API key.",
                        {"field": "api_key"},
                    )
            if record is None:
                record = WebProviderRecord(kind=kind, created_at=now)
                db.add(record)
            elif record.base_url != url or record.secret_encrypted != secret:
                record.test_ok = None
                record.test_error = None
                record.last_tested_at = None
            record.base_url = url
            record.secret_encrypted = secret
            record.updated_at = now
            db.flush()
            return self._settings(db)

    def delete_provider(self, kind: str) -> WebSettings:
        """Remove a provider; a Workspace kind that used it is left without one."""
        with Session(self._engine) as db, db.begin():
            record = db.get(WebProviderRecord, kind)
            if record is None:
                raise _unknown_kind(kind)
            db.delete(record)
            row = db.get(WebSettingsRecord, 1)
            if row is not None:
                if row.normal_provider == kind:
                    row.normal_provider = None
                if row.internal_provider == kind:
                    row.internal_provider = None
                row.updated_at = self._clock()
            db.flush()
            return self._settings(db)

    def test_provider(self, kind: str) -> WebProvider:
        """Run one search through the provider and keep what happened."""
        with Session(self._engine) as db:
            record = db.get(WebProviderRecord, kind)
            if record is None:
                raise _unknown_kind(kind)
            provider = self._provider(record)
        try:
            provider.search(TEST_QUERY, limit=1)
            ok, error = True, None
        except SearchError as exc:
            ok, error = False, str(exc)
        with Session(self._engine) as db, db.begin():
            record = db.get(WebProviderRecord, kind)
            if record is None:
                raise _unknown_kind(kind)
            record.test_ok = ok
            record.test_error = error
            record.last_tested_at = self._clock()
            db.flush()
            return _provider_view(record)

    # -- the assistant's tools -------------------------------------------------------------

    def search_provider(self, workspace_id: uuid.UUID) -> str | None:
        """The provider the Workspace's searches go to; ``None``: web search is not offered
        (off, or no provider for this kind of Workspace). Never an external provider for an
        internal-only Workspace."""
        with Session(self._engine) as db:
            row = db.get(WebSettingsRecord, 1)
            if row is None or not row.enabled:
                return None
            internal_only = self._internal_only(workspace_id)
            kind = row.internal_provider if internal_only else row.normal_provider
            if kind is None or kind not in KINDS:
                return None
            if internal_only and not KINDS[kind].internal:
                return None
            if db.get(WebProviderRecord, kind) is None:
                return None
            return kind

    def fetch_available(self, workspace_id: uuid.UUID) -> bool:
        """Web fetch shares the switch. An internal-only Workspace reads the web only once
        the admin gave internal-only Workspaces web access (an internal provider)."""
        with Session(self._engine) as db:
            row = db.get(WebSettingsRecord, 1)
            if row is None or not row.enabled:
                return False
        if not self._internal_only(workspace_id):
            return True
        return self.search_provider(workspace_id) is not None

    def search(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID, query: str, *, limit: int = 5
    ) -> WebSearch:
        kind = self.search_provider(workspace_id)
        if kind is None:
            raise ApiError(403, "web_search_off", "Web search is not available here.")
        sent = strip_values(query)
        if not sent:
            raise ApiError(
                422,
                "empty_query",
                "Nothing is left of the query once values are removed: search for concepts, "
                "never for data values.",
            )
        size = max(1, min(limit, MAX_RESULTS))
        failures: list[str] = []
        for attempt in self._search_order(kind):
            # Failover: re-check the kind right before the call (ADR 0008).
            if (
                attempt != kind
                and self._internal_only(workspace_id)
                and not KINDS[attempt].internal
            ):
                continue
            provider = self._attempt(workspace_id, actor_id, attempt, sent, first=attempt == kind)
            if provider is None:
                continue
            try:
                return WebSearch(attempt, sent, provider.search(sent, limit=size))
            except SearchError as exc:
                failures.append(str(exc))
        raise ApiError(
            502,
            "web_search_failed",
            "Web search is unavailable right now. " + " ".join(failures),
        )

    def _search_order(self, chosen: str) -> list[str]:
        """The chosen provider, then every other registered one that could answer (a
        LangSearch with no saved key cannot)."""
        with Session(self._engine) as db:
            others = [
                r.kind
                for r in db.scalars(sa.select(WebProviderRecord).order_by(WebProviderRecord.kind))
                if r.kind != chosen
                and r.kind in KINDS
                and (r.kind == "searxng" or r.secret_encrypted is not None)
            ]
        return [chosen, *others[:1]]

    def _attempt(
        self, workspace_id: uuid.UUID, actor_id: uuid.UUID, kind: str, sent: str, *, first: bool
    ) -> SearchProvider | None:
        """Audit the attempt, then hand back its provider; ``None``: it cannot be tried."""
        with Session(self._engine) as db, db.begin():
            record = db.get(WebProviderRecord, kind)
            if record is None:
                if first:
                    raise ApiError(403, "web_search_off", "Web search is not available here.")
                return None
            try:
                provider = self._provider(record)
            except ApiError:
                if first:
                    raise
                return None
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=actor_id,
                entity_type=AUDIT_SEARCH,
                entity_id=kind,
                old=None,
                new={"query": sent, "provider": kind},
                at=self._clock(),
                via="ai",
            )
        return provider

    def fetch(self, actor_id: uuid.UUID, workspace_id: uuid.UUID, url: str) -> FetchedPage:
        if not self.fetch_available(workspace_id):
            raise ApiError(403, "web_fetch_off", "Web fetch is not available here.")
        host = (urlsplit(url.strip()).hostname or "")[:64] or "unknown"
        with Session(self._engine) as db, db.begin():
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=actor_id,
                entity_type=AUDIT_FETCH,
                entity_id=host,
                old=None,
                new={"url": redact_text(url.strip(), entities=False)[:MAX_AUDIT_URL_CHARS]},
                at=self._clock(),
                via="ai",
            )
        try:
            return fetch_page(self._network, url)
        except FetchError as exc:
            status = 403 if exc.code == "address_blocked" else 502
            raise ApiError(status, exc.code, exc.message) from None

    # -- helpers -------------------------------------------------------------------------

    def _seal(self, secret: str) -> str:
        return self._box.encrypt(secret, context=API_KEY_CONTEXT)

    def _provider(self, record: WebProviderRecord) -> SearchProvider:
        if record.kind == "searxng":
            return Searxng(record.base_url, self._network)
        try:
            key = self._box.decrypt(record.secret_encrypted or "", context=API_KEY_CONTEXT)
        except DecryptionError:
            raise ApiError(
                409, "api_key_unreadable", "The stored API key cannot be read: enter it again."
            ) from None
        return LangSearch(key, self._network, url=record.base_url)

    def _settings(self, db: Session) -> WebSettings:
        row = db.get(WebSettingsRecord, 1)
        providers = [
            _provider_view(r)
            for r in db.scalars(sa.select(WebProviderRecord).order_by(WebProviderRecord.kind))
        ]
        return WebSettings(
            enabled=row.enabled if row else False,
            normal_provider=row.normal_provider if row else None,
            internal_provider=row.internal_provider if row else None,
            providers=providers,
            updated_at=row.updated_at if row else None,
        )


def _provider_view(record: WebProviderRecord) -> WebProvider:
    kind = KINDS[record.kind]
    return WebProvider(
        kind=record.kind,
        label=kind.label,
        internal=kind.internal,
        base_url=record.base_url,
        has_api_key=record.secret_encrypted is not None,
        test_ok=record.test_ok,
        test_error=record.test_error,
        last_tested_at=record.last_tested_at,
        updated_at=record.updated_at,
    )
