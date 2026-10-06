"""Arabic normalisation for full-text search (spec §6.17).

Applied to a document's text before indexing and to the query before searching, so the
two always meet: diacritics (and the tatweel stretch) are removed; every alef form
becomes a plain alef, alef maqsura becomes ya and ta marbuta becomes ha.
"""

from __future__ import annotations

import re

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


def words(text: str) -> list[str]:
    """The distinct normalised words of ``text``, in order of first appearance."""
    return list(dict.fromkeys(_WORD.findall(normalise(text))))
