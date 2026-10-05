"""The sample source database on MySQL and on MariaDB (spec §10, ticket #48).

``mysql_source`` is parametrised over both servers, so every test that takes it runs
twice. Import it where it is used (it is not a fixture for every test):

    from tests.sample_source.mysql import mysql_source  # noqa: F401
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pymysql
import pytest
from testcontainers.community.mysql import MySqlContainer

SEED = Path(__file__).with_name("seed_mysql.sql")
IMAGES = ("mysql:8.4", "mariadb:11.4")
ADMIN = ("root", "admin-secret")


@dataclass(frozen=True)
class MySqlSource:
    image: str
    host: str
    port: int
    database: str
    reader: tuple[str, str]
    writer: tuple[str, str]
    admin: tuple[str, str]

    @property
    def is_mariadb(self) -> bool:
        return self.image.startswith("mariadb")

    def connection_body(self, *, user: str = "reader", **overrides: object) -> dict[str, object]:
        """A ``PUT .../connection`` body for this server (``user``: reader, writer, admin)."""
        username, password = {"reader": self.reader, "writer": self.writer, "admin": self.admin}[
            user
        ]
        body: dict[str, object] = {
            "engine": "mysql",
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": username,
            "password": password,
            "allowed_schemas": ["core", "crm"],
        }
        body.update(overrides)
        return body


def _connect_when_ready(host: str, port: int) -> pymysql.Connection:
    """The container logs "ready for connections" once for its init server and again for
    the real one; connect until the real one answers."""
    deadline = time.monotonic() + 60
    while True:
        try:
            connection = pymysql.connect(
                host=host,
                port=port,
                user=ADMIN[0],
                password=ADMIN[1],
                charset="utf8mb4",
                autocommit=True,
            )
            connection.ping()
            return connection
        except pymysql.err.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


@pytest.fixture(scope="session", params=IMAGES)
def mysql_source(request: pytest.FixtureRequest) -> Iterator[MySqlSource]:
    image: str = request.param
    container = MySqlContainer(image, root_password=ADMIN[1])
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(3306))
        connection = _connect_when_ready(host, port)
        with connection, connection.cursor() as cur:
            for statement in re.split(r"^-- go$", SEED.read_text(encoding="utf-8"), flags=re.M):
                body = "\n".join(
                    line for line in statement.splitlines() if not line.startswith("--")
                ).strip()
                if body:
                    cur.execute(body)
            # What a Connection's user needs to see routine bodies (see the user docs).
            cur.execute(
                "GRANT SELECT ON mysql.proc TO 'dawam_reader'@'%', 'dawam_writer'@'%'"
                if image.startswith("mariadb")
                else "GRANT SHOW_ROUTINE ON *.* TO 'dawam_reader'@'%', 'dawam_writer'@'%'"
            )
        yield MySqlSource(
            image=image,
            host=host,
            port=port,
            database="core",
            reader=("dawam_reader", "reader-secret"),
            writer=("dawam_writer", "writer-secret"),
            admin=ADMIN,
        )
