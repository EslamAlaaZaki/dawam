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

A page DAWAM cannot read itself goes down a chain of readers when the fetch says it may
(``reader_may_try``), or when the page is empty or thin (#547: a reader's text is used only if
it is longer and not a block page); the first read that is used stops the chain:

- Crawl4AI (#545), a browser on the installation's own locked-down network, when
  ``DAWAM_CRAWL4AI_URL`` is set. Internal-only Workspaces and URLs with a query string may use it.
- Jina Reader, an outside service, only when the URL has no query string and the Workspace is
  not internal-only. The optional key (``DAWAM_JINA_API_KEY``) is only ever sent to Jina.

There is no setting for either beyond the web access switch. One fetch spends at most
``FETCH_BUDGET_SECONDS`` across all its steps.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_text

from .internal.crawl4ai import READER as CRAWL4AI_READER
from .internal.crawl4ai import read_through_crawl4ai
from .internal.fetch import FetchedPage, FetchError, fetch_page, looks_blocked
from .internal.jina import READER as JINA_READER
from .internal.jina import READER_TIMEOUT_SECONDS as JINA_TIMEOUT_SECONDS
from .internal.jina import read_through_jina
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
FETCH_BUDGET_SECONDS = 45.0
"""One ``fetch``, across its own fetch and every reader (#545)."""
MIN_JINA_SECONDS = 5.0
"""Jina is skipped with less than this left: it could not finish."""
MIN_CRAWL4AI_SECONDS = 25.0
"""Crawl4AI is skipped with less than this left: a page holds it for up to 20 s plus a 4 s delay."""


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
        jina_api_key: str | None = None,
        crawl4ai_url: str | None = None,
        crawl4ai_api_token: str | None = None,
    ) -> None:
        self._engine = engine
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._network = network or SystemNetwork()
        self._internal_only = internal_only
        self._jina_api_key = jina_api_key
        self._crawl4ai_url = crawl4ai_url
        self._crawl4ai_api_token = crawl4ai_api_token

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
            provider = self._attempt(
                workspace_id, actor_id, attempt, sent, first=attempt == kind, failures=failures
            )
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
        """The chosen provider, then one other registered provider to fail over to."""
        with Session(self._engine) as db:
            others = [
                r.kind
                for r in db.scalars(sa.select(WebProviderRecord).order_by(WebProviderRecord.kind))
                if r.kind != chosen and r.kind in KINDS
            ]
        return [chosen, *others[:1]]

    def _attempt(
        self,
        workspace_id: uuid.UUID,
        actor_id: uuid.UUID,
        kind: str,
        sent: str,
        *,
        first: bool,
        failures: list[str],
    ) -> SearchProvider | None:
        """Audit the attempt, then hand back its provider; ``None``: it cannot be tried. An
        unreadable key is a failed attempt (its message joins ``failures``), so the search
        can fail over."""
        with Session(self._engine) as db, db.begin():
            record = db.get(WebProviderRecord, kind)
            if record is None:
                if first:
                    raise ApiError(403, "web_search_off", "Web search is not available here.")
                return None
            try:
                provider = self._provider(record)
            except ApiError as exc:
                failures.append(exc.message)
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
        deadline = monotonic() + FETCH_BUDGET_SECONDS
        self._audit_fetch(actor_id, workspace_id, url)
        try:
            page = fetch_page(self._network, url, deadline=deadline, clock=monotonic)
        except FetchError as exc:
            if exc.reader_may_try:
                read = next(self._reader_reads(actor_id, workspace_id, url, deadline), None)
                if read is not None:
                    return read
            status = 403 if exc.code == "address_blocked" else 502
            raise ApiError(status, exc.code, exc.message) from None
        if page.text and not page.thin:
            return page
        # Empty or thin (#547): the readers get the same chance as after an error, and the
        # longer text wins. Nothing is lost: failed or shorter reads leave DAWAM's own text.
        for read in self._reader_reads(actor_id, workspace_id, url, deadline):
            if len(read.text) > len(page.text) and not (
                page.text and looks_blocked(read.title, read.text)
            ):
                return read  # a thin page's own text is not replaced by a block page
        if not page.text:
            raise ApiError(502, "empty_page", "The page has no text to read.")
        return page

    def _reader_reads(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID, url: str, deadline: float
    ) -> Iterator[FetchedPage]:
        """What each reader in the chain could read. One at a time: the caller stops the chain
        by not asking for the next, so nothing further is audited or sent."""
        if (read := self._read_through_crawl4ai(actor_id, workspace_id, url, deadline)) is not None:
            yield read
        if (read := self._read_through_jina(actor_id, workspace_id, url, deadline)) is not None:
            yield read

    def _crawl4ai_target(self, workspace_id: uuid.UUID, url: str) -> str | None:
        """What may be sent to Crawl4AI, ``None``: nothing may. It stays on this network, so
        the query string may too; the switch is read again here, right before the call."""
        if self._crawl4ai_url is None or not self.fetch_available(workspace_id):
            return None
        return urlunsplit(urlsplit(url.strip())._replace(fragment=""))

    def _jina_target(self, workspace_id: uuid.UUID, url: str) -> str | None:
        """What may be sent to Jina, ``None``: nothing may. A query string can carry a token or
        data; a fragment is not part of the address. Internal-only and the switch are read
        again here, right before the call, not when the fetch began."""
        parts = urlsplit(url.strip())
        if parts.query:
            return None
        if self._internal_only(workspace_id) or not self.fetch_available(workspace_id):
            return None
        return urlunsplit(parts._replace(fragment=""))

    def _read_through_crawl4ai(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID, url: str, deadline: float
    ) -> FetchedPage | None:
        """The page through Crawl4AI, ``None`` when it was not tried or could not read it."""
        target = self._crawl4ai_target(workspace_id, url)
        if (
            target is None
            or self._crawl4ai_url is None
            or deadline - monotonic() < MIN_CRAWL4AI_SECONDS
        ):
            return None
        self._audit_fetch(actor_id, workspace_id, target, reader=CRAWL4AI_READER)
        try:
            return read_through_crawl4ai(
                self._network,
                self._crawl4ai_url,
                target,
                api_token=self._crawl4ai_api_token,
            )
        except FetchError:
            return None

    def _read_through_jina(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID, url: str, deadline: float
    ) -> FetchedPage | None:
        """The page through Jina, ``None`` when it was not tried or could not read it."""
        target = self._jina_target(workspace_id, url)
        left = deadline - monotonic()
        if target is None or left < MIN_JINA_SECONDS:
            return None
        self._audit_fetch(actor_id, workspace_id, target, reader=JINA_READER)
        try:
            return read_through_jina(
                self._network,
                target,
                api_key=self._jina_api_key,
                timeout=min(JINA_TIMEOUT_SECONDS, left),
            )
        except FetchError:
            return None

    def _audit_fetch(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID, url: str, *, reader: str | None = None
    ) -> None:
        host = (urlsplit(url.strip()).hostname or "")[:64] or "unknown"
        new = {"url": redact_text(url.strip(), entities=False)[:MAX_AUDIT_URL_CHARS]}
        if reader is not None:
            new["reader"] = reader
        with Session(self._engine) as db, db.begin():
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=actor_id,
                entity_type=AUDIT_FETCH,
                entity_id=host,
                old=None,
                new=new,
                at=self._clock(),
                via="ai",
            )

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
