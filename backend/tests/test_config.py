"""Settings come only from DAWAM_* env vars; startup fails clearly without a required secret."""

import base64
import os
import secrets

import pytest
from alembic import command
from pydantic import ValidationError

from dawam.__main__ import main
from dawam.platform.config import ConfigError, Settings, decode_encryption_key, load_settings
from dawam.platform.db import create_engine
from dawam.platform.migrations import alembic_config, current_revisions, head_revisions

DATABASE_URL = "postgresql+psycopg://dawam:dawam@localhost:5432/dawam"


def new_key(*, urlsafe: bool = True, size: int = 32) -> str:
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return encode(secrets.token_bytes(size)).decode()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    """No DAWAM_* variables and no .env file, except what each test sets."""
    for name in list(os.environ):
        if name.startswith("DAWAM_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAWAM_DATABASE_URL", DATABASE_URL)


def test_settings_are_read_from_the_environment(monkeypatch):
    key = new_key()
    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", key)
    monkeypatch.setenv("DAWAM_HSTS_MAX_AGE_SECONDS", "600")

    settings = load_settings()

    assert settings.database_url == DATABASE_URL
    assert settings.encryption_key.get_secret_value() == base64.urlsafe_b64decode(key)
    assert settings.hsts_max_age_seconds == 600


def test_the_encryption_key_is_never_shown_in_the_settings(monkeypatch):
    key = new_key()
    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", key)

    settings = load_settings()

    raw = settings.encryption_key.get_secret_value()
    for shown in (repr(settings), str(settings), str(settings.model_dump())):
        assert key not in shown
        assert str(raw) not in shown


def test_a_rejected_key_is_not_shown_in_validation_errors():
    bad_key = new_key(size=31)
    with pytest.raises(ValidationError) as raised:
        Settings(database_url=DATABASE_URL, encryption_key=bad_key)  # type: ignore[arg-type]

    assert bad_key not in str(raised.value)
    assert bad_key not in repr(raised.value)


@pytest.mark.parametrize("urlsafe", [True, False])
def test_a_32_byte_base64_key_is_accepted(monkeypatch, urlsafe):
    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", new_key(urlsafe=urlsafe))

    load_settings()


def test_the_settings_hold_the_decoded_32_byte_key(monkeypatch):
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", base64.b64encode(raw).decode())

    assert load_settings().encryption_key.get_secret_value() == raw


@pytest.mark.parametrize(
    "text",
    [
        "{std}",
        "{url}",
        "{std_unpadded}",
        "{url_unpadded}",
        "  {url}\n",
        "\t{url_unpadded} ",
    ],
)
def test_keys_decode_with_or_without_padding_and_surrounding_whitespace(text):
    raw = secrets.token_bytes(32)
    forms = {
        "std": base64.b64encode(raw).decode(),
        "url": base64.urlsafe_b64encode(raw).decode(),
    }
    forms |= {f"{name}_unpadded": value.rstrip("=") for name, value in dict(forms).items()}

    assert decode_encryption_key(text.format(**forms)) == raw


def test_a_token_urlsafe_key_is_accepted():
    text = secrets.token_urlsafe(32)  # 43 characters, no padding

    assert len(decode_encryption_key(text)) == 32


@pytest.mark.parametrize(
    "text",
    ["", "   ", "not a key!", new_key(size=31).rstrip("="), new_key(size=33), "a b" * 15],
)
def test_anything_but_exactly_32_decoded_bytes_is_rejected(text):
    with pytest.raises(ValueError):
        decode_encryption_key(text)


def test_startup_fails_clearly_without_the_encryption_key():
    with pytest.raises(ConfigError) as raised:
        load_settings()

    message = str(raised.value)
    assert "DAWAM_ENCRYPTION_KEY is not set" in message
    assert "secrets.token_bytes(32)" in message  # says how to generate one


@pytest.mark.parametrize(
    "bad_key",
    ["", "not a key!", new_key(size=16), new_key(size=64), "c2hvcnQ"],
)
def test_startup_fails_clearly_with_a_malformed_encryption_key(monkeypatch, bad_key):
    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", bad_key)

    with pytest.raises(ConfigError) as raised:
        load_settings()

    message = str(raised.value)
    assert "DAWAM_ENCRYPTION_KEY" in message
    assert "32 bytes" in message
    if bad_key:
        assert bad_key not in message  # never echo a secret
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_every_problem_is_reported_at_once(monkeypatch):
    monkeypatch.delenv("DAWAM_DATABASE_URL")

    with pytest.raises(ConfigError) as raised:
        load_settings()

    assert "DAWAM_DATABASE_URL is not set" in str(raised.value)
    assert "DAWAM_ENCRYPTION_KEY is not set" in str(raised.value)


@pytest.mark.parametrize("command", ["serve", "worker"])
def test_the_command_line_exits_with_the_message_instead_of_a_traceback(capsys, command):
    assert main([command]) == 2

    err = capsys.readouterr().err
    assert "DAWAM_ENCRYPTION_KEY is not set" in err
    assert "Traceback" not in err


def test_serve_trusts_proxy_headers_only_from_the_configured_addresses(monkeypatch):
    import uvicorn

    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", new_key())
    monkeypatch.setenv("DAWAM_FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append(options))

    assert main(["serve"]) == 0

    (options,) = calls
    assert options["proxy_headers"] is True
    assert options["forwarded_allow_ips"] == "10.0.0.0/8"


def test_migrations_from_the_command_line_need_only_the_database_url(
    monkeypatch, fresh_database_url
):
    """``alembic upgrade head`` in backend/ never touches the encryption key."""
    monkeypatch.setenv("DAWAM_DATABASE_URL", fresh_database_url)

    command.upgrade(alembic_config(), "head")  # env.py reads DAWAM_DATABASE_URL itself

    engine = create_engine(fresh_database_url)
    try:
        assert current_revisions(engine) == head_revisions()
    finally:
        engine.dispose()


def test_migrations_from_the_command_line_fail_clearly_without_the_database_url(monkeypatch):
    monkeypatch.delenv("DAWAM_DATABASE_URL")

    with pytest.raises(ConfigError) as raised:
        command.upgrade(alembic_config(), "head")

    assert "DAWAM_DATABASE_URL is not set" in str(raised.value)
    assert "DAWAM_ENCRYPTION_KEY" not in str(raised.value)


def test_the_admin_email_without_its_password_is_explained(monkeypatch):
    monkeypatch.setenv("DAWAM_ENCRYPTION_KEY", new_key())
    monkeypatch.setenv("DAWAM_ADMIN_EMAIL", "admin@example.com")

    with pytest.raises(ConfigError) as raised:
        load_settings()

    assert "  - set both DAWAM_ADMIN_EMAIL and DAWAM_ADMIN_PASSWORD, or neither." in str(
        raised.value
    )
