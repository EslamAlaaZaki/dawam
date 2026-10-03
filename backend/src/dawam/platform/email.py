"""Outgoing email.

Code sends mail through an ``EmailSender``; which implementation is used is decided
in the composition root (``dawam.app``):

- ``LoggingEmailSender``: default until SMTP delivery exists (#26). It logs
  that a message was sent (recipient and subject only, never the body, which may
  carry one-time links).
- ``InMemoryOutbox``: keeps every message in memory so tests can read what was sent.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class LoggingEmailSender:
    def send(self, message: EmailMessage) -> None:
        logger.info("email sent", extra={"email_to": message.to, "email_subject": message.subject})


class InMemoryOutbox:
    """An ``EmailSender`` that captures messages instead of delivering them."""

    def __init__(self) -> None:
        self._messages: list[EmailMessage] = []
        self._lock = threading.Lock()

    def send(self, message: EmailMessage) -> None:
        with self._lock:
            self._messages.append(message)

    @property
    def messages(self) -> list[EmailMessage]:
        with self._lock:
            return list(self._messages)

    def sent_to(self, address: str) -> list[EmailMessage]:
        return [m for m in self.messages if m.to == address]

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
