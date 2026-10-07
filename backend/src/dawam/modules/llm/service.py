"""LLM providers and models: the admin's registry, and the gateway for each model.

Authorization is the caller's job (the router restricts everything to admins); the
service trusts its caller. API keys are sealed with ``SecretBox`` before they are
stored and never read back out through this API: callers get ``has_api_key``. A stored
key is reused only for the base URL it was saved for, so a hijacked admin session
cannot send it to a server of its choosing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlsplit

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .gateway import Capabilities, Gateway, ProbeResult, probe
from .internal.adapters import AdapterConfig, AdapterFactory, adapter_for, adapter_kinds
from .tables import (
    MODEL_NAME_MAX_LENGTH,
    NAME_MAX_LENGTH,
    URL_MAX_LENGTH,
    ModelRecord,
    ProviderRecord,
)

API_KEY_CONTEXT = "llm.provider.api_key"
API_KEY_MAX_LENGTH = 1000
ROLES = ("agent", "light", "embedding")
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600
MAX_CONTEXT_WINDOW = 100_000_000


@dataclass(frozen=True)
class Model:
    id: uuid.UUID
    provider_id: uuid.UUID
    name: str
    roles: list[str]
    context_window: int | None
    tool_calling: bool | None
    streaming: bool | None
    json_schema: bool | None
    embedding_dimension: int | None
    test_ok: bool | None
    test_error_code: str | None
    test_error: str | None
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def limited(self) -> bool:
        """Tested and without native tool calling: shown as "limited" (spec §6.18)."""
        return self.test_ok is True and self.tool_calling is False and "embedding" not in self.roles


@dataclass(frozen=True)
class Provider:
    id: uuid.UUID
    name: str
    adapter: str
    base_url: str
    has_api_key: bool
    is_internal: bool
    timeout_seconds: int
    models: list[Model]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProviderInput:
    """What an admin types. ``api_key``: ``None`` keeps the stored one (allowed only for
    the base URL it was saved for), ``""`` clears it."""

    name: str
    base_url: str
    is_internal: bool
    adapter: str = "openai_compatible"
    api_key: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ModelInput:
    name: str
    roles: Sequence[str]
    context_window: int | None = None
    """The admin's own figure; the test fills it from the server's metadata if left out."""


@dataclass(frozen=True)
class SetupStatus:
    """Whether the installation can run AI (spec §6.18): at least one agent model that
    passed "Test connection"."""

    complete: bool
    has_agent_model: bool
    has_tested_agent_model: bool
    has_internal_agent_model: bool
    """Without one, new Workspaces default to not internal-only."""


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_llm_config", message, {"field": field})


def _text(value: str, field: str, label: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise _invalid(f"The {label} must not be empty.", field)
    if len(cleaned) > max_length:
        raise _invalid(f"The {label} must be at most {max_length} characters.", field)
    return cleaned


def _clean_url(value: str, adapter: str = "") -> str:
    url = _text(value, "base_url", "base URL", URL_MAX_LENGTH).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise _invalid("The base URL must start with http:// or https://.", "base_url")
    # Azure OpenAI carries its API version as the one allowed query parameter.
    query_ok = not parts.query or (
        adapter == "azure_openai"
        and [key for key, _ in parse_qsl(parts.query, keep_blank_values=True)] == ["api-version"]
    )
    if parts.username or parts.password or not query_ok or parts.fragment:
        raise _invalid("The base URL must not hold credentials, a query or a fragment.", "base_url")
    return url


def _clean_roles(roles: Sequence[str]) -> list[str]:
    unknown = [role for role in roles if role not in ROLES]
    if unknown:
        raise _invalid(f"Unknown role {unknown[0]!r}. Roles: {', '.join(ROLES)}.", "roles")
    cleaned = [role for role in ROLES if role in roles]
    if not cleaned:
        raise _invalid("Give the model at least one role.", "roles")
    if "embedding" in cleaned and len(cleaned) > 1:
        raise _invalid("An embedding model cannot also chat: register it on its own.", "roles")
    return cleaned


def _clean_window(value: int | None) -> int | None:
    if value is not None and not 1 <= value <= MAX_CONTEXT_WINDOW:
        raise _invalid("The context window must be a positive number of tokens.", "context_window")
    return value


def _model_view(record: ModelRecord) -> Model:
    return Model(
        id=record.id,
        provider_id=record.provider_id,
        name=record.name,
        roles=list(record.roles),
        context_window=record.context_window,
        tool_calling=record.supports_tools,
        streaming=record.supports_streaming,
        json_schema=record.supports_json_schema,
        embedding_dimension=record.embedding_dimension,
        test_ok=record.test_ok,
        test_error_code=record.test_error_code,
        test_error=record.test_error,
        last_tested_at=record.last_tested_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _provider_view(record: ProviderRecord, models: Sequence[ModelRecord]) -> Provider:
    return Provider(
        id=record.id,
        name=record.name,
        adapter=record.adapter,
        base_url=record.base_url,
        has_api_key=record.secret_encrypted is not None,
        is_internal=record.is_internal,
        timeout_seconds=record.timeout_seconds,
        models=[_model_view(m) for m in models],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _reset_test(record: ModelRecord) -> None:
    record.test_ok = None
    record.test_error_code = None
    record.test_error = None
    record.last_tested_at = None
    record.supports_tools = None
    record.supports_streaming = None
    record.supports_json_schema = None
    record.embedding_dimension = None


class ProviderService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        encryption_key: bytes,
        clock: Clock,
        adapters: AdapterFactory = adapter_for,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._engine = engine
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._adapters = adapters
        self._sleep = sleep

    # -- providers -------------------------------------------------------------------

    def list_providers(self) -> list[Provider]:
        with Session(self._engine) as db:
            models: dict[uuid.UUID, list[ModelRecord]] = {}
            for model in db.scalars(sa.select(ModelRecord).order_by(ModelRecord.name)):
                models.setdefault(model.provider_id, []).append(model)
            providers = db.scalars(sa.select(ProviderRecord).order_by(ProviderRecord.name))
            return [_provider_view(p, models.get(p.id, [])) for p in providers]

    def get_provider(self, provider_id: uuid.UUID) -> Provider:
        with Session(self._engine) as db:
            return self._provider_view(db, self._load_provider(db, provider_id))

    def create_provider(self, data: ProviderInput) -> Provider:
        data = self._clean(data)
        now = self._clock()
        record = ProviderRecord(
            id=uuid.uuid4(),
            name=data.name,
            adapter=data.adapter,
            base_url=data.base_url,
            secret_encrypted=self._seal(data.api_key),
            is_internal=data.is_internal,
            timeout_seconds=data.timeout_seconds,
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as db:
            db.add(record)
            self._commit(db, "provider_name_taken", "A provider with this name already exists.")
            return _provider_view(record, [])

    def update_provider(self, provider_id: uuid.UUID, data: ProviderInput) -> Provider:
        """Replace the provider's settings. Changing the base URL or the key drops the
        test results of its models (they were found out against something else)."""
        data = self._clean(data)
        with Session(self._engine) as db:
            record = self._load_provider(db, provider_id, lock=True)
            if (
                data.api_key is None
                and record.secret_encrypted is not None
                and record.base_url != data.base_url
            ):
                raise ApiError(
                    422,
                    "api_key_required",
                    "Enter the API key again: the stored one is only used for the base URL "
                    "it was saved for.",
                    {"field": "api_key"},
                )
            reachability_changed = record.base_url != data.base_url or data.api_key is not None
            if data.api_key is not None:
                record.secret_encrypted = self._seal(data.api_key)
            record.name = data.name
            record.adapter = data.adapter
            record.base_url = data.base_url
            record.is_internal = data.is_internal
            record.timeout_seconds = data.timeout_seconds
            record.updated_at = self._clock()
            if reachability_changed:
                for model in self._models_of(db, provider_id):
                    _reset_test(model)
            self._commit(db, "provider_name_taken", "A provider with this name already exists.")
            return self._provider_view(db, record)

    def delete_provider(self, provider_id: uuid.UUID) -> None:
        with Session(self._engine) as db, db.begin():
            record = self._load_provider(db, provider_id)
            db.delete(record)

    # -- models ----------------------------------------------------------------------

    def add_model(self, provider_id: uuid.UUID, data: ModelInput) -> Model:
        name, roles = self._clean_model(data)
        now = self._clock()
        with Session(self._engine) as db:
            self._load_provider(db, provider_id)
            record = ModelRecord(
                id=uuid.uuid4(),
                provider_id=provider_id,
                name=name,
                roles=roles,
                context_window=_clean_window(data.context_window),
                created_at=now,
                updated_at=now,
            )
            _reset_test(record)
            db.add(record)
            self._commit(db, "model_exists", "This provider already has a model with that name.")
            return _model_view(record)

    def update_model(self, model_id: uuid.UUID, data: ModelInput) -> Model:
        """Replace the model's name, roles and context window. A new name drops its test
        result."""
        name, roles = self._clean_model(data)
        with Session(self._engine) as db:
            record = self._load_model(db, model_id, lock=True)
            if record.name != name or record.roles != roles:
                _reset_test(record)
            record.name = name
            record.roles = roles
            record.context_window = _clean_window(data.context_window)
            record.updated_at = self._clock()
            self._commit(db, "model_exists", "This provider already has a model with that name.")
            return _model_view(record)

    def delete_model(self, model_id: uuid.UUID) -> None:
        with Session(self._engine) as db, db.begin():
            db.delete(self._load_model(db, model_id))

    def test_model(self, model_id: uuid.UUID) -> Model:
        """ "Test connection": run the probe against the provider and record the result.
        A failure is recorded (``test_ok = False`` with its code and a safe message), not
        raised. The probe talks to the provider outside any database transaction."""
        with Session(self._engine) as db:
            model = self._load_model(db, model_id)
            provider = self._load_provider(db, model.provider_id)
            name, roles, window = model.name, list(model.roles), model.context_window
            tested_provider_state = (provider.base_url, provider.secret_encrypted)
            gateway = self._gateway(provider, name)
        result: ProbeResult = probe(
            gateway,
            chat="embedding" not in roles,
            embedding="embedding" in roles,
            context_window=window,
        )
        with Session(self._engine) as db, db.begin():
            record = self._load_model(db, model_id, lock=True)
            provider = self._load_provider(db, record.provider_id)
            if (
                record.name != name
                or (provider.base_url, provider.secret_encrypted) != tested_provider_state
            ):
                return _model_view(record)  # changed meanwhile: the result is stale
            caps = result.capabilities
            record.test_ok = result.ok
            record.test_error_code = result.error_code
            record.test_error = result.error
            record.last_tested_at = self._clock()
            record.supports_tools = caps.tool_calling
            record.supports_streaming = caps.streaming
            record.supports_json_schema = caps.json_schema
            record.embedding_dimension = caps.embedding_dimension
            if record.context_window is None:
                record.context_window = caps.context_window
            return _model_view(record)

    # -- the gateway -----------------------------------------------------------------

    def gateway_for(self, model_id: uuid.UUID) -> Gateway:
        """The gateway for a registered model, carrying what its last test found. Other
        modules reach every model only through this."""
        with Session(self._engine) as db:
            model = self._load_model(db, model_id)
            provider = self._load_provider(db, model.provider_id)
            return self._gateway(provider, model.name, model)

    def setup_status(self) -> SetupStatus:
        with Session(self._engine) as db:
            rows = db.execute(
                sa.select(ModelRecord.test_ok, ProviderRecord.is_internal)
                .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
                .where(sa.literal("agent") == sa.any_(ModelRecord.roles))
            ).all()
        tested = [internal for ok, internal in rows if ok]
        return SetupStatus(
            complete=bool(tested),
            has_agent_model=bool(rows),
            has_tested_agent_model=bool(tested),
            has_internal_agent_model=any(tested),
        )

    # -- internals -------------------------------------------------------------------

    def _gateway(
        self, provider: ProviderRecord, model_name: str, model: ModelRecord | None = None
    ) -> Gateway:
        config = AdapterConfig(
            base_url=provider.base_url,
            api_key=self._open(provider),
            timeout_seconds=provider.timeout_seconds,
        )
        capabilities = None
        if model is not None:
            capabilities = Capabilities(
                tool_calling=model.supports_tools,
                streaming=model.supports_streaming,
                json_schema=model.supports_json_schema,
                context_window=model.context_window,
                embedding_dimension=model.embedding_dimension,
            )
        return Gateway(
            self._adapters(provider.adapter, config),
            model=model_name,
            capabilities=capabilities,
            sleep=self._sleep,
        )

    def _seal(self, api_key: str | None) -> str | None:
        return self._box.encrypt(api_key, context=API_KEY_CONTEXT) if api_key else None

    def _open(self, provider: ProviderRecord) -> str | None:
        if provider.secret_encrypted is None:
            return None
        try:
            return self._box.decrypt(provider.secret_encrypted, context=API_KEY_CONTEXT)
        except DecryptionError:
            raise ApiError(
                409,
                "llm_secret_unreadable",
                "The stored API key does not decrypt with DAWAM_ENCRYPTION_KEY. "
                "Enter the API key again.",
            ) from None

    @staticmethod
    def _clean(data: ProviderInput) -> ProviderInput:
        kinds = adapter_kinds()
        if data.adapter not in kinds:
            raise _invalid(f"The adapter must be one of: {', '.join(kinds)}.", "adapter")
        if not 1 <= data.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise _invalid(
                f"The timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds.",
                "timeout_seconds",
            )
        if data.api_key is not None and len(data.api_key) > API_KEY_MAX_LENGTH:
            raise _invalid(
                f"The API key must be at most {API_KEY_MAX_LENGTH} characters.", "api_key"
            )
        return ProviderInput(
            name=_text(data.name, "name", "name", NAME_MAX_LENGTH),
            base_url=_clean_url(data.base_url, data.adapter),
            is_internal=data.is_internal,
            adapter=data.adapter,
            api_key=data.api_key,
            timeout_seconds=data.timeout_seconds,
        )

    @staticmethod
    def _clean_model(data: ModelInput) -> tuple[str, list[str]]:
        return (
            _text(data.name, "name", "model name", MODEL_NAME_MAX_LENGTH),
            _clean_roles(data.roles),
        )

    @staticmethod
    def _commit(db: Session, code: str, message: str) -> None:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ApiError(409, code, message) from None

    @staticmethod
    def _models_of(db: Session, provider_id: uuid.UUID) -> list[ModelRecord]:
        return list(
            db.scalars(
                sa.select(ModelRecord)
                .where(ModelRecord.provider_id == provider_id)
                .order_by(ModelRecord.name)
            )
        )

    def _provider_view(self, db: Session, record: ProviderRecord) -> Provider:
        return _provider_view(record, self._models_of(db, record.id))

    @staticmethod
    def _load_provider(
        db: Session, provider_id: uuid.UUID, *, lock: bool = False
    ) -> ProviderRecord:
        query = sa.select(ProviderRecord).where(ProviderRecord.id == provider_id)
        record = db.scalars(query.with_for_update() if lock else query).first()
        if record is None:
            raise ApiError(404, "provider_not_found", "Provider not found.")
        return record

    @staticmethod
    def _load_model(db: Session, model_id: uuid.UUID, *, lock: bool = False) -> ModelRecord:
        query = sa.select(ModelRecord).where(ModelRecord.id == model_id)
        record = db.scalars(query.with_for_update() if lock else query).first()
        if record is None:
            raise ApiError(404, "model_not_found", "Model not found.")
        return record
