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


_ARABIC_LETTER = re.compile("[ء-يٱ-ۓ]")
_LATIN_LETTER = re.compile("[A-Za-z]")


def language(text: str) -> str | None:
    """``ar`` or ``en`` by the script most of the letters are in; ``None`` without letters
    in either script."""
    arabic, latin = len(_ARABIC_LETTER.findall(text)), len(_LATIN_LETTER.findall(text))
    if not arabic and not latin:
        return None
    return "ar" if arabic >= latin else "en"


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


def stems(text: str) -> list[str]:
    """The distinct stems of the words of ``text``, in order of first appearance."""
    return list(dict.fromkeys(stem(w) for w in words(text)))
