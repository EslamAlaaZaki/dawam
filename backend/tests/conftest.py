"""S1 test harness.

Tests drive the HTTP API through a test client against a real PostgreSQL 16 started
with Testcontainers (once per test session). Fixtures:

- ``anonymous_client``: a test client with no session.
- ``signed_in_client``: a test client signed in as ``signed_in_user`` (a regular
  user) through the API; it sends the CSRF token on every request.
- ``admin_client``: the same, signed in as ``admin_user`` (an admin).
- ``create_user``: creates a user (default: a regular user) and returns its
  credentials; sign in with ``tests.helpers.sign_in``.
- ``clock``: the app's clock, a ``FakeClock`` the test moves with ``advance``.
- ``outbox``: captures every email the app sends (``outbox.messages``); it stands in
  for the SMTP server, so emails reach it once SMTP settings are saved (without
  them, links are kept for admins). ``outbox.fail_with(reason)`` makes sends fail.
- ``jobs``: the job runner; in tests background work always runs inline.
- ``fake_llm``: the scripted fake LLM provider behind every registered provider.
- ``fresh_database_url``: an empty, unmigrated database for tests that need one.
- ``roles``: a client per permission-matrix role (anonymous, non-member user,
  viewer, editor, owner, non-member admin) on one Workspace; see ``tests/roles.py``.

The container starts lazily, so tests that need no database don't pay for it.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from dawam.app import Services, create_app
from dawam.modules.auth import AuthService, SystemRole
from dawam.modules.jobs import InlineJobRunner
from dawam.modules.llm import FakeAdapter
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.db import Base
from dawam.platform.email import InMemoryOutbox
from tests.helpers import csrf_token, sign_in
from tests.roles import RoleClients
from tests.sample_source import sample_source  # noqa: F401  (fixture for every test)
from tests.sample_source.sqlserver import (  # noqa: F401  (fixtures; the container starts on use)
    sample_source_sqlserver,
    sqlserver,
)

POSTGRES_IMAGE = "pgvector/pgvector:pg16"
TEST_ENCRYPTION_KEY = secrets.token_bytes(32)
"""A fresh encryption key per test run; tests never need a real one."""


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres: PostgresContainer) -> str:
    return postgres.get_connection_url()


@pytest.fixture
def fresh_database_url(database_url: str) -> Iterator[str]:
    """An empty database in the test PostgreSQL server, dropped afterwards."""
    name = f"fresh_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield sa.make_url(database_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(
        database_url=database_url,
        storage_path=str(tmp_path / "files"),
        encryption_key=TEST_ENCRYPTION_KEY,  # type: ignore[arg-type]
    )


@pytest.fixture
def outbox() -> InMemoryOutbox:
    return InMemoryOutbox()


@pytest.fixture
def jobs() -> InlineJobRunner:
    return InlineJobRunner()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))


@pytest.fixture
def services(outbox: InMemoryOutbox, jobs: InlineJobRunner, clock: FakeClock) -> Services:
    return Services(email=outbox, jobs=jobs, clock=clock)


@pytest.fixture
def fake_llm(services: Services) -> FakeAdapter:
    """The scripted fake LLM provider every provider of the app uses: queue replies with
    ``fake_llm.script(Reply(...))``; ``fake_llm.calls`` records what was asked."""
    fake = FakeAdapter()
    services.llm_adapters = lambda kind, config: fake
    return fake


@pytest.fixture
def app(settings: Settings, services: Services) -> Iterator[FastAPI]:
    application = create_app(settings, services=services)
    yield application
    _reset_database(application.state.engine)


@pytest.fixture
def anonymous_client(app: FastAPI) -> Iterator[TestClient]:
    """A client with no session. Entering it runs startup (incl. migrations)."""
    with TestClient(app) as client:
        yield client


@dataclass(frozen=True)
class CreatedUser:
    id: uuid.UUID
    email: str
    password: str
    display_name: str


UserFactory = Callable[..., CreatedUser]


@pytest.fixture
def auth_service(app: FastAPI, anonymous_client: TestClient, clock: FakeClock) -> AuthService:
    """The auth module's service on the test app (startup has migrated the database)."""
    return AuthService(app.state.engine, app.state.settings, clock=clock)


@pytest.fixture
def create_user(auth_service: AuthService) -> UserFactory:
    """Create a user through the auth module's service."""
    service = auth_service

    def create(
        email: str = "grace@example.com",
        password: str = "correct horse battery",
        display_name: str = "Grace Hopper",
        system_role: SystemRole = "user",
    ) -> CreatedUser:
        user = service.create_user(
            email=email, password=password, display_name=display_name, system_role=system_role
        )
        return CreatedUser(
            id=user.id, email=user.email, password=password, display_name=display_name
        )

    return create


@pytest.fixture
def signed_in_user(create_user: UserFactory) -> CreatedUser:
    return create_user(
        email="ada@example.com", password="analytical engine", display_name="Ada Lovelace"
    )


@pytest.fixture
def signed_in_client(app: FastAPI, signed_in_user: CreatedUser) -> Iterator[TestClient]:
    """A client signed in as ``signed_in_user`` (a regular user) through the API.

    It carries the session cookie and sends the CSRF token on every request, as the
    frontend does. It is separate from ``anonymous_client``, so a test can use both.
    """
    with TestClient(app) as client:
        response = sign_in(client, signed_in_user.email, signed_in_user.password)
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER] = csrf_token(client)
        yield client


@pytest.fixture
def roles(app: FastAPI, auth_service: AuthService, clock: FakeClock) -> Iterator[RoleClients]:
    """A client per permission-matrix role on one Workspace (see ``tests/roles.py``)."""
    with ExitStack() as stack:
        yield RoleClients(app, auth_service, WorkspaceService(app.state.engine, clock=clock), stack)


@pytest.fixture
def admin_user(create_user: UserFactory) -> CreatedUser:
    return create_user(
        email="root@example.com",
        password="administrator password",
        display_name="Root Admin",
        system_role="admin",
    )


@pytest.fixture
def admin_client(app: FastAPI, admin_user: CreatedUser) -> Iterator[TestClient]:
    """A client signed in as ``admin_user`` (an admin) through the API, sending the
    CSRF token on every request like ``signed_in_client``."""
    with TestClient(app) as client:
        response = sign_in(client, admin_user.email, admin_user.password)
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER] = csrf_token(client)
        yield client


def _reset_database(engine: sa.Engine) -> None:
    """Empty every application table between tests; the schema itself is kept."""
    tables = [table.name for table in Base.metadata.sorted_tables]
    if tables:
        with engine.begin() as conn:
            conn.execute(sa.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    engine.dispose()
