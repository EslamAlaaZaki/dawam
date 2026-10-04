from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.email import (
    Delivery,
    EmailDeliveryError,
    EmailMessage,
    EmailSender,
    OneTimeLink,
    SmtpConfig,
    SmtpSecurity,
)
from dawam.platform.errors import ApiError

from .tables import SMTP_SETTINGS_ID, SmtpSettingsRecord, UndeliveredLinkRecord

logger = logging.getLogger(__name__)

_PASSWORD_CONTEXT = "smtp.password"
_LINK_CONTEXT = "mail.undelivered_link"

# One address, no display name, and nothing that could start another header line.
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
_HOST = re.compile(r"^[A-Za-z0-9.:\[\]_-]+$")

UndeliveredReason = Literal["smtp_not_configured", "smtp_failed"]


class _Keep(Enum):
    KEEP = "keep"


KEEP_PASSWORD: Final = _Keep.KEEP
"""Pass as ``password`` to keep the stored SMTP password."""


def is_valid_address(address: str) -> bool:
    return len(address) <= 320 and _ADDRESS.match(address) is not None


@dataclass(frozen=True)
class SmtpSettings:
    """The saved SMTP settings, as the API shows them: never the password."""

    host: str
    port: int
    security: SmtpSecurity
    username: str | None
    has_password: bool
    sender: str
    updated_at: datetime


@dataclass(frozen=True)
class UndeliveredLink:
    id: uuid.UUID
    recipient: str
    subject: str
    purpose: str
    url: str
    reason: UndeliveredReason
    created_at: datetime
    expires_at: datetime


def _settings(record: SmtpSettingsRecord) -> SmtpSettings:
    return SmtpSettings(
        host=record.host,
        port=record.port,
        security=record.security,  # type: ignore[arg-type]  # the check constraint holds it
        username=record.username,
        has_password=record.password_encrypted is not None,
        sender=record.sender,
        updated_at=record.updated_at,
    )


class MailService:
    """The one email-delivery service every module sends through (a ``Mailer``).

    With SMTP settings saved, ``send`` delivers through the ``EmailSender`` the
    composition root chose. Without them, or when delivery fails, a message that
    carries a one-time link is kept as an undelivered link, sealed, for an admin to
    copy to the recipient until the link expires (spec §6.1, "Without SMTP").
    """

    def __init__(
        self, engine: sa.Engine, settings: Settings, *, sender: EmailSender, clock: Clock
    ) -> None:
        self._engine = engine
        self._box = SecretBox(settings.encryption_key.get_secret_value())
        self._sender = sender
        self._clock = clock

    # --- Delivery -------------------------------------------------------------------

    def send(self, message: EmailMessage, *, link: OneTimeLink | None = None) -> Delivery:
        """Deliver ``message``, or keep its ``link`` for an admin when it cannot be.

        Never raises for a delivery failure: it is logged (without the body, which
        may carry the link) and the outcome returned."""
        reason: UndeliveredReason = "smtp_not_configured"
        smtp = self._smtp_config()
        if smtp is not None:
            try:
                self._sender.send(message, smtp)
                return "sent"
            except EmailDeliveryError as exc:
                logger.warning(
                    "email not delivered",
                    extra={"email_subject": message.subject, "problem": str(exc)},
                )
                reason = "smtp_failed"
        if link is None:
            logger.warning(
                "email not sent: SMTP is not set up", extra={"email_subject": message.subject}
            )
            return "not_sent"
        with Session(self._engine) as db, db.begin():
            db.add(
                UndeliveredLinkRecord(
                    recipient=message.to,
                    subject=message.subject[:300],
                    purpose=link.purpose,
                    url_encrypted=self._box.encrypt(link.url, context=_LINK_CONTEXT),
                    reason=reason,
                    created_at=self._clock(),
                    expires_at=link.expires_at,
                )
            )
        logger.info(
            "email link kept for an admin to share",
            extra={"email_subject": message.subject, "reason": reason},
        )
        return "link_for_admin"

    # --- SMTP settings --------------------------------------------------------------

    def smtp_settings(self) -> SmtpSettings | None:
        with Session(self._engine) as db:
            record = db.get(SmtpSettingsRecord, SMTP_SETTINGS_ID)
            return _settings(record) if record else None

    def save_smtp_settings(
        self,
        *,
        host: str,
        port: int,
        security: SmtpSecurity,
        sender: str,
        username: str | None,
        password: str | _Keep | None = KEEP_PASSWORD,
        by: uuid.UUID | None = None,
    ) -> SmtpSettings:
        """Save the SMTP settings. ``username`` None means no authentication (and no
        password). The stored password is kept only while host, port and username stay
        the same: moving it to another server needs it entered again.

        Raises ``ApiError`` 422: ``invalid_smtp_host``, ``invalid_sender``,
        ``smtp_password_required``."""
        host = host.strip()
        sender = sender.strip()
        username = (username or "").strip() or None
        if not host or _HOST.match(host) is None:
            raise ApiError(422, "invalid_smtp_host", "The SMTP host is not a host name.")
        if not is_valid_address(sender):
            raise ApiError(422, "invalid_sender", "The sender must be one email address.")
        with Session(self._engine) as db, db.begin():
            record = db.get(SmtpSettingsRecord, SMTP_SETTINGS_ID, with_for_update=True)
            sealed: str | None
            if username is None or password is None:
                sealed = None
            elif isinstance(password, str):
                sealed = self._box.encrypt(password, context=_PASSWORD_CONTEXT)
            elif record is None or record.password_encrypted is None:
                sealed = None
            elif (record.host, record.port, record.username) != (host, port, username):
                raise ApiError(
                    422,
                    "smtp_password_required",
                    "Enter the SMTP password again to use it with another server or username.",
                )
            else:
                sealed = record.password_encrypted
            if record is None:
                record = SmtpSettingsRecord(id=SMTP_SETTINGS_ID)
                db.add(record)
            record.host = host
            record.port = port
            record.security = security
            record.username = username
            record.password_encrypted = sealed
            record.sender = sender
            record.updated_at = self._clock()
            record.updated_by = by
            db.flush()
            return _settings(record)

    def clear_smtp_settings(self) -> None:
        """Turn SMTP off: links are kept for admins again."""
        with Session(self._engine) as db, db.begin():
            db.execute(sa.delete(SmtpSettingsRecord))

    def send_test(self, to: str) -> None:
        """Send a test email through the saved settings, now.

        Raises ``ApiError``: 422 ``invalid_email``, 409 ``smtp_not_configured``, 502
        ``smtp_failed`` (the message says why)."""
        to = to.strip()
        if not is_valid_address(to):
            raise ApiError(422, "invalid_email", "The email address is not valid.")
        smtp = self._smtp_config()
        if smtp is None:
            raise ApiError(409, "smtp_not_configured", "Save the SMTP settings first.")
        message = EmailMessage(
            to=to,
            subject="DAWAM test email",
            body="This is a test email from DAWAM: its SMTP settings work.\n",
        )
        try:
            self._sender.send(message, smtp)
        except EmailDeliveryError as exc:
            raise ApiError(502, "smtp_failed", str(exc)) from None

    def _smtp_config(self) -> SmtpConfig | None:
        with Session(self._engine) as db:
            record = db.get(SmtpSettingsRecord, SMTP_SETTINGS_ID)
            if record is None:
                return None
            password = None
            if record.password_encrypted is not None:
                try:
                    password = self._box.decrypt(
                        record.password_encrypted, context=_PASSWORD_CONTEXT
                    )
                except DecryptionError:
                    # Saved under another DAWAM_ENCRYPTION_KEY: unusable until re-entered.
                    logger.error(
                        "the stored SMTP password does not decrypt with DAWAM_ENCRYPTION_KEY; "
                        "enter it again in the SMTP settings"
                    )
            return SmtpConfig(
                host=record.host,
                port=record.port,
                security=record.security,  # type: ignore[arg-type]
                sender=record.sender,
                username=record.username,
                password=password,
            )

    # --- Undelivered links ----------------------------------------------------------

    def undelivered_links(self) -> list[UndeliveredLink]:
        """The links that still work, newest first. Expired ones are deleted."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.delete(UndeliveredLinkRecord).where(UndeliveredLinkRecord.expires_at <= now)
            )
            records = db.scalars(
                sa.select(UndeliveredLinkRecord).order_by(
                    UndeliveredLinkRecord.created_at.desc(), UndeliveredLinkRecord.id
                )
            )
            links = []
            for r in records:
                try:
                    url = self._box.decrypt(r.url_encrypted, context=_LINK_CONTEXT)
                except DecryptionError:
                    continue  # sealed under another key: nobody can use it any more
                links.append(
                    UndeliveredLink(
                        id=r.id,
                        recipient=r.recipient,
                        subject=r.subject,
                        purpose=r.purpose,
                        url=url,
                        reason=r.reason,  # type: ignore[arg-type]
                        created_at=r.created_at,
                        expires_at=r.expires_at,
                    )
                )
            return links

    def dismiss_link(self, link_id: uuid.UUID) -> None:
        """Forget an undelivered link (once shared, or never to be). Raises ``ApiError``
        404 ``not_found`` if there is none with this id."""
        with Session(self._engine) as db, db.begin():
            deleted = db.execute(
                sa.delete(UndeliveredLinkRecord).where(UndeliveredLinkRecord.id == link_id)
            )
            if deleted.rowcount == 0:  # type: ignore[attr-defined]
                raise ApiError(404, "not_found", "There is no such undelivered link.")
