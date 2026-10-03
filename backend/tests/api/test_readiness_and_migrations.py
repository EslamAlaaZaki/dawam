"""/readyz is OK only when the database is reachable and migrations are at head;
the app migrates the database on startup."""

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.app import Services, create_app
from dawam.platform.config import Settings
from dawam.platform.migrations import alembic_config

UNREACHABLE_DB = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere"


def head_revisions() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def applied_revisions(database_url: str) -> set[str]:
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as conn:
            if not sa.inspect(conn).has_table("alembic_version"):
                return set()
            return set(conn.scalars(sa.text("SELECT version_num FROM alembic_version")))
    finally:
        engine.dispose()


def client_for(settings: Settings, services: Services) -> TestClient:
    return TestClient(create_app(settings, services=services))


def test_there_is_exactly_one_migration_head():
    assert len(head_revisions()) == 1


def test_startup_migrates_an_empty_database_to_head(fresh_database_url, settings, services):
    assert applied_revisions(fresh_database_url) == set()

    with client_for(settings.model_copy(update={"database_url": fresh_database_url}), services):
        pass

    assert applied_revisions(fresh_database_url) == head_revisions()


def test_startup_can_skip_migrations(fresh_database_url, settings, services):
    settings = settings.model_copy(
        update={"database_url": fresh_database_url, "run_migrations_on_startup": False}
    )

    with client_for(settings, services) as client:
        response = client.get("/readyz")

    assert applied_revisions(fresh_database_url) == set()
    assert response.status_code == 503
    assert response.json()["error"]["details"]["checks"]["migrations"] == "pending"


def test_readyz_is_ok_when_database_is_reachable_and_migrated(anonymous_client):
    response = anonymous_client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "migrations": "ok"}}


def test_readyz_fails_when_migrations_are_behind(app: FastAPI, anonymous_client, database_url):
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "base")
    try:
        response = anonymous_client.get("/readyz")
    finally:
        command.upgrade(config, "head")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "not_ready",
            "message": "DAWAM is not ready to serve requests.",
            "details": {"checks": {"database": "ok", "migrations": "pending"}},
        }
    }


def test_readyz_fails_when_the_database_is_unreachable(settings, services):
    settings = settings.model_copy(
        update={"database_url": UNREACHABLE_DB, "run_migrations_on_startup": False}
    )

    with client_for(settings, services) as client:
        ready = client.get("/readyz")
        healthy = client.get("/healthz")

    assert ready.status_code == 503
    assert ready.json()["error"]["details"]["checks"] == {
        "database": "unreachable",
        "migrations": "unknown",
    }
    assert healthy.status_code == 200
