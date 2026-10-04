from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from dawam.platform.errors import ApiError

from .internal.domains import (
    MAX_ALLOWED_DOMAINS,
    InvalidDomainError,
    email_domain,
    normalize_domains,
)
from .tables import SystemSettingRecord

_REGISTRATION_ENABLED = "registration_enabled"
_REGISTRATION_DOMAINS = "registration_allowed_email_domains"


@dataclass(frozen=True)
class RegistrationSettings:
    """Self-registration (spec story 20): off by default. ``allowed_email_domains``
    empty means any domain may sign up; otherwise only an email whose domain is exactly
    one of them (a subdomain must be listed on its own)."""

    enabled: bool = False
    allowed_email_domains: tuple[str, ...] = ()


class SystemSettingsService:
    """The installation-wide settings an admin manages (the ``system_settings`` table).

    It also answers the auth module's ``RegistrationPolicy`` questions, so the
    composition root hands it to auth for sign-up."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def registration(self) -> RegistrationSettings:
        with Session(self._engine) as db:
            values = self._read(db, (_REGISTRATION_ENABLED, _REGISTRATION_DOMAINS))
        return RegistrationSettings(
            enabled=bool(values.get(_REGISTRATION_ENABLED, False)),
            allowed_email_domains=tuple(values.get(_REGISTRATION_DOMAINS, ())),
        )

    def set_registration(
        self, *, enabled: bool, allowed_email_domains: Sequence[str]
    ) -> RegistrationSettings:
        """Replace the registration settings. Domains are stored trimmed, lower-cased,
        without a leading ``@`` or duplicates. Raises ``ApiError`` 422
        ``invalid_email_domain`` (nothing changes then)."""
        if len(allowed_email_domains) > MAX_ALLOWED_DOMAINS:
            raise ApiError(
                422,
                "invalid_email_domain",
                f"At most {MAX_ALLOWED_DOMAINS} allowed email domains can be set.",
            )
        try:
            domains = normalize_domains(allowed_email_domains)
        except InvalidDomainError as exc:
            raise ApiError(
                422,
                "invalid_email_domain",
                f"{exc.domain!r} is not a domain name (e.g. example.com).",
                {"domain": exc.domain},
            ) from None
        with Session(self._engine) as db, db.begin():
            self._write(db, {_REGISTRATION_ENABLED: enabled, _REGISTRATION_DOMAINS: domains})
        return RegistrationSettings(enabled=enabled, allowed_email_domains=tuple(domains))

    # --- the auth module's RegistrationPolicy ---------------------------------------

    def registration_open(self) -> bool:
        return self.registration().enabled

    def email_domain_allowed(self, email: str) -> bool:
        domains = self.registration().allowed_email_domains
        return not domains or email_domain(email) in domains

    # --- storage -----------------------------------------------------------------------

    @staticmethod
    def _read(db: Session, keys: Sequence[str]) -> dict[str, Any]:
        rows = db.execute(
            sa.select(SystemSettingRecord.key, SystemSettingRecord.value).where(
                SystemSettingRecord.key.in_(keys)
            )
        )
        return {key: value for key, value in rows}

    @staticmethod
    def _write(db: Session, values: dict[str, Any]) -> None:
        statement = insert(SystemSettingRecord).values(
            [{"key": key, "value": value} for key, value in values.items()]
        )
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[SystemSettingRecord.key], set_={"value": statement.excluded.value}
            )
        )
