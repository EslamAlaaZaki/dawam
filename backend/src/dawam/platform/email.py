"""Outgoing email: the sending primitive.

This is the transport only. Modules never call it directly: they send through the
``mail`` module's delivery service (``dawam.modules.mail``), which knows the SMTP
settings an admin saved, and records the link for an admin to copy when there are
none (spec §6.1, "Without SMTP"). Code that cannot import ``mail`` (``auth``, which
every module depends on) gets that service as a ``Mailer``, the port below.

Which ``EmailSender`` the delivery service uses is decided in the composition root
(``dawam.app``):

- ``SmtpEmailSender``: delivers over SMTP with the settings it is given.
- ``InMemoryOutbox``: keeps every message in memory so tests can read what was sent.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

SmtpSecurity = Literal["none", "starttls", "tls"]
"""``none``: plain SMTP (port 25); ``starttls``: upgrade to TLS after connecting (587);
``tls``: TLS from the start (465). TLS certificates are always verified."""


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: SmtpSecurity
    sender: str
    """The ``From`` address."""
    username: str | None = None
    password: str | None = field(default=None, repr=False)


class EmailDeliveryError(Exception):
    """The SMTP server could not be reached or refused the message. The message says
    why, and never contains the password."""


class EmailSender(Protocol):
    def send(self, message: EmailMessage, smtp: SmtpConfig) -> None:
        """Deliver ``message`` through the server ``smtp`` describes; raises
        ``EmailDeliveryError`` if it cannot."""
        ...


class SmtpEmailSender:
    def __init__(
        self,
        *,
        timeout: float = 15,
        ssl_context: ssl.SSLContext | None = None,
        local_hostname: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._local_hostname = local_hostname
        """The name sent in ``EHLO``; by default this machine's fully qualified name."""

    def send(self, message: EmailMessage, smtp: SmtpConfig) -> None:
        try:
            mime = MimeMessage()
            mime["From"] = smtp.sender
            mime["To"] = message.to
            mime["Subject"] = message.subject
            mime["Date"] = formatdate(localtime=False, usegmt=True)
            mime["Message-ID"] = make_msgid(domain=smtp.sender.rpartition("@")[2] or None)
            mime.set_content(message.body)
        except ValueError as exc:
            # A header with a line break (header injection) or otherwise malformed.
            raise EmailDeliveryError(f"The message cannot be sent: {exc}") from None
        try:
            with self._connect(smtp) as client:
                if smtp.username:
                    client.login(smtp.username, smtp.password or "")
                client.send_message(mime)
        except smtplib.SMTPAuthenticationError:
            raise EmailDeliveryError("The SMTP server rejected the username or password.") from None
        except smtplib.SMTPException as exc:
            raise EmailDeliveryError(f"The SMTP server refused the message: {exc}") from None
        except ssl.SSLError as exc:
            raise EmailDeliveryError(f"TLS with the SMTP server failed: {exc.reason}") from None
        except OSError as exc:
            raise EmailDeliveryError(
                f"Could not reach the SMTP server {smtp.host}:{smtp.port}: {exc}"
            ) from None
        logger.info("email sent", extra={"email_to": message.to, "email_subject": message.subject})

    def _connect(self, smtp: SmtpConfig) -> smtplib.SMTP:
        if smtp.security == "tls":
            return smtplib.SMTP_SSL(
                smtp.host,
                smtp.port,
                local_hostname=self._local_hostname,
                timeout=self._timeout,
                context=self._ssl_context,
            )
        client = smtplib.SMTP(
            smtp.host, smtp.port, local_hostname=self._local_hostname, timeout=self._timeout
        )
        if smtp.security == "starttls":
            try:
                client.starttls(context=self._ssl_context)
            except BaseException:
                client.close()
                raise
        return client


class InMemoryOutbox:
    """An ``EmailSender`` that captures messages instead of delivering them."""

    def __init__(self) -> None:
        self._sent: list[tuple[EmailMessage, SmtpConfig | None]] = []
        self._failure: str | None = None
        self._lock = threading.Lock()

    def send(self, message: EmailMessage, smtp: SmtpConfig | None = None) -> None:
        with self._lock:
            if self._failure is not None:
                raise EmailDeliveryError(self._failure)
            self._sent.append((message, smtp))

    def fail_with(self, reason: str | None) -> None:
        """Make every ``send`` raise ``EmailDeliveryError(reason)``, as an unreachable
        SMTP server would; ``None`` delivers again."""
        with self._lock:
            self._failure = reason

    @property
    def messages(self) -> list[EmailMessage]:
        with self._lock:
            return [message for message, _ in self._sent]

    @property
    def last_smtp(self) -> SmtpConfig | None:
        """The SMTP settings the last message was sent with."""
        with self._lock:
            return self._sent[-1][1] if self._sent else None

    def sent_to(self, address: str) -> list[EmailMessage]:
        return [m for m in self.messages if m.to == address]

    def clear(self) -> None:
        with self._lock:
            self._sent.clear()


# --- The delivery-service port ------------------------------------------------------

Delivery = Literal["sent", "link_for_admin", "not_sent"]
"""What became of a message: delivered over SMTP; not delivered, but its link kept
for an admin to copy; or not delivered at all (no SMTP and no link)."""


@dataclass(frozen=True)
class OneTimeLink:
    """The link a message carries, such as a password-reset link."""

    url: str
    purpose: str
    """What it is for, e.g. ``password_reset``; shown to the admin."""
    expires_at: datetime
    """After this the link no longer works, and an admin is no longer shown it."""


class Mailer(Protocol):
    """The email-delivery service (implemented by the ``mail`` module)."""

    def send(self, message: EmailMessage, *, link: OneTimeLink | None = None) -> Delivery: ...

    def withdraw_links(self, *, recipient: str, purpose: str) -> None:
        """Forget the links of ``purpose`` kept for an admin to give ``recipient``,
        because they no longer work (e.g. a reset link once the password is reset)."""
        ...
