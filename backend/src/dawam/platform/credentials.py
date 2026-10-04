"""The rules for account emails and passwords (spec §6.1).

They live in the kernel because both the settings (the bootstrap admin) and the auth
module apply them; the auth module owns everything else about accounts.
"""

from __future__ import annotations

import re

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024
"""Longer passwords are rejected before hashing, so argon2 never gets huge inputs."""
MAX_EMAIL_LENGTH = 254

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    """Emails are compared trimmed and lower-cased."""
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    """Whether a normalised email looks like an address (``name@domain.tld``)."""
    return len(email) <= MAX_EMAIL_LENGTH and _EMAIL.match(email) is not None


def password_problem(password: str) -> str | None:
    """Why ``password`` breaks the policy, or None if it is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"must be at least {MIN_PASSWORD_LENGTH} characters long"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"must be at most {MAX_PASSWORD_LENGTH} characters long"
    return None
