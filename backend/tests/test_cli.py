"""``python -m dawam``: the entry point of the app, worker and OpenAPI export."""

import json

from dawam.__main__ import main, openapi_spec
from dawam.app import create_app
from dawam.platform.config import Settings


def test_openapi_command_writes_the_apps_spec(tmp_path):
    output = tmp_path / "openapi.json"

    assert main(["openapi", "--output", str(output)]) == 0

    text = output.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    app = create_app(Settings(database_url="postgresql+psycopg://unused@localhost/unused"))
    assert json.loads(text) == app.openapi()


def test_openapi_spec_is_stable_text():
    assert openapi_spec() == openapi_spec()
