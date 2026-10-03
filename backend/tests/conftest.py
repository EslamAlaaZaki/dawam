"""S1 test harness.

Tests drive the HTTP API through a test client against a real PostgreSQL 16 started
with Testcontainers (once per test session). Fixtures:

- ``anonymous_client``: a test client with no session.
- ``signed_in_client``: extension point; becomes real when auth lands (#23).
- ``outbox``: captures every email the app sends (``outbox.messages``).
- ``jobs``: the job runner; in tests background work always runs inline.
- ``fresh_database_url``: an empty, unmigrated database for tests that need one.

The container starts lazily, so tests that need no database don't pay for it.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from dawam.app import Services, create_app
from dawam.modules.jobs import InlineJobRunner
from dawam.platform.config import Settings
from dawam.platform.db import Base
from dawam.platform.email import InMemoryOutbox

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
def services(outbox: InMemoryOutbox, jobs: InlineJobRunner) -> Services:
    return Services(email=outbox, jobs=jobs)


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


@pytest.fixture
def signed_in_client() -> TestClient:
    """Extension point for a client signed in as a regular user.

    Auth arrives in #23, which replaces this body with: create a user, sign in
    through the API and return a client carrying the session cookie and CSRF token.
    """
    pytest.fail("signed_in_client is not available until auth exists (#23)")


def _reset_database(engine: sa.Engine) -> None:
    """Empty every application table between tests; the schema itself is kept."""
    tables = [table.name for table in Base.metadata.sorted_tables]
    if tables:
        with engine.begin() as conn:
            conn.execute(sa.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    engine.dispose()
