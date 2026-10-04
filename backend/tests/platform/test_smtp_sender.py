"""``SmtpEmailSender`` delivers through a real SMTP server: plain, STARTTLS or implicit TLS,
with or without credentials. The server is aiosmtpd, in process."""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import ssl
from collections.abc import Iterator
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from pathlib import Path

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from dawam.platform.email import EmailDeliveryError, EmailMessage, SmtpConfig, SmtpEmailSender

HOST = "127.0.0.1"
USERNAME = "dawam"
PASSWORD = "smtp-password"


@dataclass
class Received:
    sender: str
    recipients: list[str]
    message: Message


@dataclass
class Mailbox:
    received: list[Received] = field(default_factory=list)

    async def handle_DATA(self, server, session, envelope):
        self.received.append(
            Received(
                sender=envelope.mail_from,
                recipients=list(envelope.rcpt_tos),
                message=message_from_bytes(envelope.content),
            )
        )
        return "250 OK"


def authenticate(server, session, envelope, mechanism, auth_data):
    ok = (
        isinstance(auth_data, LoginPassword)
        and auth_data.login == USERNAME.encode()
        and auth_data.password == PASSWORD.encode()
    )
    # handled=False: aiosmtpd then answers a failure with 535 itself.
    return AuthResult(success=ok, handled=False)


@pytest.fixture(scope="module")
def certificate(tmp_path_factory) -> tuple[Path, Path, bytes]:
    """A self-signed certificate for 127.0.0.1: (cert file, key file, PEM)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dawam-test-smtp")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(HOST))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory = tmp_path_factory.mktemp("smtp-cert")
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file, cert_pem


@pytest.fixture
def server_tls(certificate) -> ssl.SSLContext:
    cert_file, key_file, _ = certificate
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert_file, key_file)
    return context


@pytest.fixture
def trusting_client(certificate) -> ssl.SSLContext:
    """A client TLS context that trusts the test certificate (and nothing else)."""
    return ssl.create_default_context(cadata=certificate[2].decode())


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


@pytest.fixture
def run_server() -> Iterator:
    controllers: list[Controller] = []

    def start(mailbox: Mailbox, **kwargs) -> int:
        port = free_port()
        # A fixed server name: looking up this machine's own name can take seconds.
        controller = Controller(
            mailbox, hostname=HOST, port=port, server_hostname="smtp.test", **kwargs
        )
        controller.start()
        controllers.append(controller)
        return port

    yield start
    for controller in controllers:
        controller.stop()


def sender(**kwargs) -> SmtpEmailSender:
    # A fixed EHLO name: looking up this machine's own name can take seconds.
    return SmtpEmailSender(local_hostname="dawam.test", **kwargs)


def config(port: int, **overrides) -> SmtpConfig:
    values = {
        "host": HOST,
        "port": port,
        "security": "none",
        "sender": "dawam@example.com",
        "username": None,
        "password": None,
    }
    values.update(overrides)
    return SmtpConfig(**values)


MESSAGE = EmailMessage(to="ada@example.com", subject="Reset your password", body="Hello Ada")


def test_delivers_over_plain_smtp_without_credentials(run_server):
    mailbox = Mailbox()
    port = run_server(mailbox)

    sender().send(MESSAGE, config(port))

    [received] = mailbox.received
    assert received.sender == "dawam@example.com"
    assert received.recipients == ["ada@example.com"]
    assert received.message["From"] == "dawam@example.com"
    assert received.message["To"] == "ada@example.com"
    assert received.message["Subject"] == "Reset your password"
    assert received.message["Message-ID"]
    assert received.message["Date"]
    assert received.message.get_payload(decode=True).decode().strip() == "Hello Ada"


def test_delivers_over_starttls_with_credentials(run_server, server_tls, trusting_client):
    mailbox = Mailbox()
    port = run_server(
        mailbox,
        tls_context=server_tls,
        require_starttls=True,
        authenticator=authenticate,
        auth_required=True,
    )
    smtp = config(port, security="starttls", username=USERNAME, password=PASSWORD)

    sender(ssl_context=trusting_client).send(MESSAGE, smtp)

    assert [r.recipients for r in mailbox.received] == [["ada@example.com"]]


@pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")
def test_delivers_over_implicit_tls(run_server, server_tls, trusting_client):
    mailbox = Mailbox()
    port = run_server(
        mailbox,
        ssl_context=server_tls,
        authenticator=authenticate,
        auth_required=True,
        # aiosmtpd does not notice that an implicit-TLS session is encrypted.
        auth_require_tls=False,
    )
    smtp = config(port, security="tls", username=USERNAME, password=PASSWORD)

    sender(ssl_context=trusting_client).send(MESSAGE, smtp)

    assert [r.recipients for r in mailbox.received] == [["ada@example.com"]]


def test_an_untrusted_certificate_is_refused(run_server, server_tls):
    mailbox = Mailbox()
    port = run_server(mailbox, tls_context=server_tls)

    with pytest.raises(EmailDeliveryError):
        sender().send(MESSAGE, config(port, security="starttls"))

    assert mailbox.received == []


def test_wrong_credentials_fail_without_revealing_the_password(
    run_server, server_tls, trusting_client
):
    mailbox = Mailbox()
    port = run_server(
        mailbox,
        tls_context=server_tls,
        authenticator=authenticate,
        auth_required=True,
    )
    smtp = config(port, security="starttls", username=USERNAME, password="wrong-password")

    with pytest.raises(EmailDeliveryError) as raised:
        sender(ssl_context=trusting_client).send(MESSAGE, smtp)

    assert "username or password" in str(raised.value)
    assert "wrong-password" not in str(raised.value)
    assert mailbox.received == []


def test_an_unreachable_server_is_a_delivery_error():
    with pytest.raises(EmailDeliveryError):
        sender(timeout=2).send(MESSAGE, config(free_port()))


def test_the_password_is_not_in_the_configs_repr():
    assert PASSWORD not in repr(config(25, username=USERNAME, password=PASSWORD))
