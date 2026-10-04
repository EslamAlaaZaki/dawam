"""Allowed email domains for self-registration: how they are written and compared."""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_ALLOWED_DOMAINS = 200

# Lower-case DNS labels (letters, digits, inner hyphens), at least two of them. A
# non-ASCII domain is entered in its xn-- (punycode) form.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+$")


class InvalidDomainError(ValueError):
    def __init__(self, domain: str) -> None:
        super().__init__(domain)
        self.domain = domain


def normalize_domains(domains: Iterable[str]) -> list[str]:
    """The domains trimmed, lower-cased, without a leading ``@`` and without duplicates,
    in the order given. Raises ``InvalidDomainError`` for the first one that is not a
    domain name."""
    normalized: list[str] = []
    for raw in domains:
        domain = raw.strip().lower().removeprefix("@")
        if not _DOMAIN.match(domain):
            raise InvalidDomainError(raw)
        if domain not in normalized:
            normalized.append(domain)
    return normalized


def email_domain(email: str) -> str:
    """The domain of an email address (after its last ``@``), lower-cased."""
    return email.rpartition("@")[2].strip().lower()
