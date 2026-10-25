"""Stem the full-text column: Arabic and English word forms meet their stems.

Revision ID: e4b8a1c6d937
Revises: e5a1c8d7b362
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8a1c6d937"
down_revision: str | Sequence[str] | None = "e5a1c8d7b362"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 500

# A frozen copy of the normaliser and stemmer as of this revision, so the migration keeps
# producing the same column whatever later happens to the live code.
_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭـ]")
# alef with hamza above, hamza below, madda and wasla -> alef; alef maqsura -> ya;
# ta marbuta -> ha. Code points, because the letters look alike in an editor.
_LETTERS = {
    **dict.fromkeys((0x0623, 0x0625, 0x0622, 0x0671), 0x0627),
    0x0649: 0x064A,
    0x0629: 0x0647,
}
_WORD = re.compile(r"\w+")


def normalise(text: str) -> str:
    """``text`` lower-cased, without Arabic diacritics, with the letter forms unified."""
    return _DIACRITICS.sub("", text).translate(_LETTERS).casefold()


# Light stemming, applied after normalisation (so ta marbuta is already ha and alef
# maqsura is ya). Arabic: strip one definite-article/conjunction prefix and one
# plural/dual/pronoun suffix (light10 style); English: a small suffix stripper. Both keep
# a minimum stem so short words stay whole. Index and query use the same functions.
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و")
_AR_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")  # noqa: RUF001
_AR_MIN = 3
_AR_WORD = re.compile("[ء-ي]+")
_VOWELS = "aeiou"


def _stem_arabic(word: str) -> str:
    for prefix in _AR_PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= _AR_MIN:
            word = word[len(prefix) :]
            break
    for suffix in _AR_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _AR_MIN:
            return word[: -len(suffix)]
    return word


def _stem_english(word: str) -> str:
    if len(word) <= 3:
        return word[:-1] if len(word) == 3 and word.endswith("e") else word  # use -> us
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "ches", "shes", "xes", "zes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix, shortest in (("ing", 2), ("ed", 2)):
        root = word[: -len(suffix)]
        if word.endswith(suffix) and len(root) >= shortest and any(c in _VOWELS for c in root):
            word = root
            if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "lsz" + _VOWELS:
                word = word[:-1]
            break
    if word.endswith("y") and len(word) > 3:
        word = word[:-1] + "i"
    if word.endswith("e") and len(word) >= 3:
        word = word[:-1]
    return word


def stem(word: str) -> str:
    """The light stem of one normalised ``word``; digits and other scripts are left as is."""
    if _AR_WORD.fullmatch(word):
        return _stem_arabic(word)
    if word.isascii() and word.isalpha():
        return _stem_english(word)
    return word


def stemmed(text: str) -> str:
    """``text`` normalised with every word stemmed: what full-text search indexes."""
    return _WORD.sub(lambda m: stem(m.group()), normalise(text))


def upgrade() -> None:
    """Re-stem every passage; ``search_vector`` is generated from ``search_text``, so it
    follows."""
    conn = op.get_bind()
    last = None
    while True:
        rows = conn.execute(
            sa.text(
                "SELECT id, search_text FROM document_chunks "
                "WHERE (CAST(:last AS uuid) IS NULL OR id > CAST(:last AS uuid)) "
                "ORDER BY id LIMIT :n"
            ),
            {"last": last, "n": _BATCH},
        ).all()
        if not rows:
            return
        conn.execute(
            sa.text("UPDATE document_chunks SET search_text = :t WHERE id = :id"),
            [{"id": r.id, "t": stemmed(r.search_text)} for r in rows],
        )
        last = str(rows[-1].id)


def downgrade() -> None:
    """Stemming is lossy and the passage text is intact: a re-index restores the old column."""
