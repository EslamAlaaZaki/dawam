"""The sample source database on Oracle (spec §10, ticket #50).

``oracle_source`` is the sample source (``seed_oracle.sql``) in an Oracle Free container
that starts once per test session. It is slow and heavy, so only tests marked
``oracle`` use it, and the default test run leaves those out (``pytest -m oracle``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import oracledb
import pytest
from testcontainers.community.oracle import OracleDbContainer

from tests.sample_source import SampleSource

SEED = Path(__file__).with_name("seed_oracle.sql")
ORACLE_IMAGE = "gvenzl/oracle-free:slim"
SERVICE = "FREEPDB1"
ALLOWED_SCHEMAS = ["CORE", "CRM"]


def oracle_body(source: SampleSource, *, user: str = "reader", **overrides: object) -> dict:
    """A ``PUT .../connection`` body for the Oracle sample source."""
    return source.connection_body(
        user=user, engine="oracle", allowed_schemas=ALLOWED_SCHEMAS, **overrides
    )


@pytest.fixture(scope="session")
def oracle_source() -> Iterator[SampleSource]:
    with OracleDbContainer(ORACLE_IMAGE, oracle_password="sys-secret") as container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(1521))
        with oracledb.connect(
            user="system",
            password="sys-secret",
            host=host,
            port=port,
            service_name=SERVICE,
        ) as conn:
            cursor = conn.cursor()
            script = SEED.read_text(encoding="utf-8").replace("\r\n", "\n")
            for statement in script.split("\n/\n"):
                body = "\n".join(
                    line for line in statement.splitlines() if not line.startswith("--")
                ).strip()
                if body:
                    cursor.execute(body)
        yield SampleSource(
            host=host,
            port=port,
            database=SERVICE,
            reader=("DAWAM_READER", "reader-secret"),
            writer=("DAWAM_WRITER", "writer-secret"),
            admin=("SYSTEM", "sys-secret"),
        )
