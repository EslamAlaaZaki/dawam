"""The sample source database (spec §10, ticket #45's S3 seam).

``sample_source`` is a seeded PostgreSQL database that connector, extraction, inference
and PII tests use as a Source System's database. It is a second database in the test
session's PostgreSQL container. Import the fixture in a ``conftest.py`` to use it:

    from tests.sample_source import sample_source  # noqa: F401
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from testcontainers.community.postgres import PostgresContainer

SEED = Path(__file__).with_name("seed.sql")


@dataclass(frozen=True)
class SampleSource:
    host: str
    port: int
    database: str
    reader: tuple[str, str]
    """``(username, password)`` of a read-only user: the one DAWAM should be given."""
    writer: tuple[str, str]
    """A user that can write to ``core.customers`` (the write-privilege warning)."""
    admin: tuple[str, str]
    """The container's superuser."""

    def connection_body(self, *, user: str = "reader", **overrides: object) -> dict[str, object]:
        """A ``PUT .../connection`` body for this database (``user``: reader, writer, admin)."""
        username, password = {"reader": self.reader, "writer": self.writer, "admin": self.admin}[
            user
        ]
        body: dict[str, object] = {
            "engine": "postgresql",
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": username,
            "password": password,
            "allowed_schemas": ["core", "crm"],
        }
        body.update(overrides)
        return body


@pytest.fixture(scope="session")
def sample_source(postgres: PostgresContainer, database_url: str) -> Iterator[SampleSource]:
    name = f"sample_source_{uuid.uuid4().hex[:8]}"
    admin_engine = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = sa.make_url(database_url)
    with psycopg.connect(
        host=url.host, port=url.port, user=url.username, password=url.password, dbname=name
    ) as conn:
        conn.execute(SEED.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    try:
        yield SampleSource(
            host=str(url.host),
            port=int(url.port or 5432),
            database=name,
            reader=("dawam_reader", "reader-secret"),
            writer=("dawam_writer", "writer-secret"),
            admin=(str(url.username), str(url.password)),
        )
    finally:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()
