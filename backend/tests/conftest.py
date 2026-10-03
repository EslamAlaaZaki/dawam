"""S1 test harness.

Tests drive the HTTP API through a test client against a real PostgreSQL 16 started
with Testcontainers (once per test session). Fixtures:

- ``anonymous_client``: a test client with no session.
- ``signed_in_client``: a test client signed in as ``signed_in_user`` (a regular
  user) through the API; it sends the CSRF token on every request.
- ``create_user``: creates a user (default: a regular user) and returns its
  credentials; sign in with ``tests.helpers.sign_in``.
- ``clock``: the app's clock, a ``FakeClock`` the test moves with ``advance``.
- ``outbox``: captures every email the app sends (``outbox.messages``).
- ``jobs``: the job runner; in tests background work always runs inline.
- ``fresh_database_url``: an empty, unmigrated database for tests that need one.

The container starts lazily, so tests that need no database don't pay for it.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from dawam.app import Services, create_app
from dawam.modules.auth import AuthService, SystemRole
from dawam.modules.jobs import InlineJobRunner
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.db import Base
from dawam.platform.email import InMemoryOutbox
from tests.helpers import csrf_token, sign_in

POSTGRES_IMAGE = "postgres:16"
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
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        encryption_key=TEST_ENCRYPTION_KEY,  # type: ignore[arg-type]
        frontend_dist=None,
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
def create_user(app: FastAPI, anonymous_client: TestClient, clock: FakeClock) -> UserFactory:
    """Create a user through the auth module's service (startup has migrated the database)."""
    service = AuthService(app.state.engine, app.state.settings, clock=clock)

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


def _reset_database(engine: sa.Engine) -> None:
    """Empty every application table between tests; the schema itself is kept."""
    tables = [table.name for table in Base.metadata.sorted_tables]
    if tables:
        with engine.begin() as conn:
            conn.execute(sa.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    engine.dispose()
