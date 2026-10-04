"""The auth module's rules for account emails, display names and passwords (spec §6.1)."""

from __future__ import annotations

import re
import unicodedata
from functools import cache
from importlib import resources

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024
"""Longer passwords are rejected before hashing, so argon2 never gets huge inputs."""
MAX_EMAIL_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 200

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    """Emails are compared trimmed and lower-cased."""
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    """Whether a normalised email looks like an address (``name@domain.tld``)."""
    return len(email) <= MAX_EMAIL_LENGTH and _EMAIL.match(email) is not None


def normalize_display_name(display_name: str) -> str:
    """Display names are stored trimmed."""
    return display_name.strip()


def display_name_problem(display_name: str) -> str | None:
    """Why a normalised display name is not acceptable, or None if it is."""
    if not display_name:
        return "must not be empty"
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        return f"must be at most {MAX_DISPLAY_NAME_LENGTH} characters long"
    if any(unicodedata.category(char) == "Cc" for char in display_name):
        return "must not contain control characters"
    return None


@cache
def _common_passwords() -> frozenset[str]:
    """The common-password list shipped with DAWAM (``common_passwords.txt``, lower-case;
    see ``common_passwords.LICENSE.txt`` for its source). Read once, from the package, so
    it works offline."""
    text = resources.files(__package__).joinpath("common_passwords.txt").read_text("utf-8")
    return frozenset(line for line in text.splitlines() if line)


def password_problem(password: str) -> str | None:
    """Why ``password`` breaks the policy, or None if it is acceptable.

    The policy (spec §6.1): 10 to 1024 characters, and not on the common-password list,
    compared case-insensitively (``Password123`` is as guessable as ``password123``)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"must be at least {MIN_PASSWORD_LENGTH} characters long"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"must be at most {MAX_PASSWORD_LENGTH} characters long"
    if password.lower() in _common_passwords():
        return "is too common: choose one that is harder to guess"
    return None
