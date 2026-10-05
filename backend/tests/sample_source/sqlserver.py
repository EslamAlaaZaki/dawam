"""The sample source database on SQL Server (ticket #49).

``sample_source_sqlserver`` is the same seeded database as ``sample_source`` (see
``seed_sqlserver.sql``), in a SQL Server container of its own that starts only when a
test asks for it. Import the fixture in a ``conftest.py`` to use it:

    from tests.sample_source.sqlserver import sample_source_sqlserver  # noqa: F401
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pymssql
import pytest
from testcontainers.community.mssql import SqlServerContainer

from . import SampleSource

SEED = Path(__file__).with_name("seed_sqlserver.sql")
IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
ADMIN = ("sa", "Dawam-Test-1Secure")
LOGINS = (("dawam_reader", "reader-secret"), ("dawam_writer", "writer-secret"))


@pytest.fixture(scope="session")
def sqlserver() -> Iterator[SqlServerContainer]:
    with SqlServerContainer(IMAGE, username=ADMIN[0], password=ADMIN[1], dbname="master") as box:
        yield box


@pytest.fixture(scope="session")
def sample_source_sqlserver(sqlserver: SqlServerContainer) -> Iterator[SampleSource]:
    host = sqlserver.get_container_host_ip()
    port = int(sqlserver.get_exposed_port(sqlserver.port))
    name = f"sample_source_{uuid.uuid4().hex[:8]}"

    def admin(database: str):
        return pymssql.connect(
            server=host,
            port=str(port),
            user=ADMIN[0],
            password=ADMIN[1],
            database=database,
            charset="UTF-8",
            autocommit=True,
        )

    with admin("master") as conn:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE [{name}]")
        for login, password in LOGINS:
            cur.execute(
                f"IF SUSER_ID('{login}') IS NULL"
                f" CREATE LOGIN {login} WITH PASSWORD = '{password}', CHECK_POLICY = OFF"
            )
    with admin(name) as conn:
        cur = conn.cursor()
        for batch in re.split(r"(?im)^GO\s*$", SEED.read_text(encoding="utf-8")):
            if batch.strip():
                cur.execute(batch)
    try:
        yield SampleSource(
            host=host,
            port=port,
            database=name,
            reader=LOGINS[0],
            writer=LOGINS[1],
            admin=ADMIN,
            engine="sqlserver",
        )
    finally:
        with admin("master") as conn:
            conn.cursor().execute(
                f"IF DB_ID('{name}') IS NOT NULL BEGIN"
                f" ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;"
                f" DROP DATABASE [{name}] END"
            )
