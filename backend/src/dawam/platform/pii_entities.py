"""NER-based PII masking (#385): person names and addresses, in Arabic and English.

The regex and checksum validators (``pii_validators``) cannot see a name or an address, so
``redact_text`` also asks the process's ``EntityDetector`` for person and location spans and
masks them as ``[redacted: person_name]`` / ``[redacted: address]``, the rule ids of the name
rules. The detector is a port: off by default (regex only, as before), Presidio with spaCy
(English) and CAMeL Tools (Arabic) when ``DAWAM_PII_NER_ENABLED`` is on
(``pii_ner_presidio``, the optional ``ner`` extra), ``FakeEntityDetector`` in tests.

A person span is always masked. A location is masked only as part of an address: when it
holds a street word ("Olaya Street", "شارع العليا"), follows one ("حي العليا") or a house
number, or ends in a postal code; places next to it ("…, Riyadh 12211") join the address.
A city on its own ("sales in Riyadh") is ordinary prose and stays.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

EntityKind = Literal["person", "location"]


@dataclass(frozen=True)
class EntitySpan:
    start: int
    end: int
    kind: EntityKind


class EntityDetector(Protocol):
    def detect(self, text: str) -> Iterable[EntitySpan]:
        """The person and location spans in ``text`` (character offsets)."""
        ...


class NerSettings(Protocol):
    pii_ner_enabled: bool
    pii_ner_english_model: str
    pii_ner_arabic_model: str


class FakeEntityDetector:
    """Tests only: tags every occurrence of the given phrases, longest first."""

    def __init__(self, phrases: Mapping[str, EntityKind]) -> None:
        self._phrases = sorted(phrases.items(), key=lambda item: -len(item[0]))

    def detect(self, text: str) -> list[EntitySpan]:
        taken = [False] * len(text)
        spans: list[EntitySpan] = []
        for phrase, kind in self._phrases:
            for matched in re.finditer(re.escape(phrase), text):
                start, end = matched.span()
                if not any(taken[start:end]):
                    taken[start:end] = [True] * (end - start)
                    spans.append(EntitySpan(start, end, kind))
        return sorted(spans, key=lambda s: s.start)


_current: EntityDetector | None = None


def entity_detector() -> EntityDetector | None:
    """The process's detector, or ``None`` (the default: names and addresses not looked for)."""
    return _current


def set_entity_detector(detector: EntityDetector | None) -> None:
    global _current
    _current = detector


def configure_entity_detector(settings: NerSettings) -> EntityDetector | None:
    """Install the process's detector from the settings. Turned on, the engines must load: a
    missing extra or model raises, so a misconfigured installation does not silently keep
    names in saved text."""
    if not settings.pii_ner_enabled:
        set_entity_detector(None)
        return None
    from dawam.platform.pii_ner_presidio import PresidioEntityDetector

    detector = PresidioEntityDetector(
        english_model=settings.pii_ner_english_model,
        arabic_model=settings.pii_ner_arabic_model,
    )
    set_entity_detector(detector)
    logger.info("NER-based PII masking is on")
    return detector


# -- masking ----------------------------------------------------------------------------

_STREET_WORDS = (
    r"street|st|road|rd|avenue|ave|lane|boulevard|blvd|highway|district|building|bldg|"
    r"apartment|apt|suite|floor|p\.?\s?o\.?\s?box|"
    r"شارع|طريق|حي|مبنى|عمارة|شقة|الدور|ص\.?\s?ب|صندوق بريد"
)
_STREET_WORD = re.compile(rf"(?<!\w)(?:{_STREET_WORDS})(?!\w)", re.IGNORECASE)
_WORD_BEFORE = re.compile(rf"(?<!\w)(?:{_STREET_WORDS})\.?\s*:?\s*$", re.IGNORECASE)
_NUMBER_BEFORE = re.compile(r"(?<![\w.])\d{1,6}[A-Za-z]?,?\s+$")
_WORD = r"[^\W\d_][^\s,،.:;!?؟()]*"
_STREETS = (
    # "12 Olaya Street", "45 King Abdullah Rd." (NER models often miss street names)
    re.compile(
        r"(?<![\w.])(?:\d{1,6}[A-Za-z]?\s+)?(?:[A-Z][\w'\-]*\s+){1,4}"
        r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Boulevard|Blvd|Highway)(?!\w)\.?"
    ),
    # "شارع العليا", "حي النخيل", "طريق الملك فهد"
    re.compile(rf"(?<!\w)(?:شارع|طريق|حي)\s+{_WORD}(?:\s+{_WORD}){{0,2}}"),
    re.compile(r"(?<!\w)(?:P\.?\s?O\.?\s?Box|ص\.?\s?ب)\.?\s*\d{1,6}(?!\d)", re.IGNORECASE),
)
_POSTAL_CODE = re.compile(r"[ \t]*,?[ \t]*\d{5}(?:-\d{4})?(?!\d)")
_CITY_POSTAL = re.compile(
    rf"[ \t]*[,،]?[ \t]*{_WORD}(?:[ \t]+{_WORD})?[ \t]+\d{{5}}(?:-\d{{4}})?(?!\d)"
)
_JOINED = re.compile(r"[\s,،\-]*")
_MARKER = re.compile(r"\[redacted: [a-z_]+\]")
_LETTER = re.compile(r"[^\W\d_]")


@dataclass(frozen=True)
class _Place:
    start: int
    end: int
    street: bool
    """Already looks like an address on its own (a street phrase, a street word inside)."""


def _overlaps(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


def mask_entities(text: str, detector: EntityDetector, mark: Callable[[str], str]) -> str:
    """``text`` with the detector's names and addresses replaced by ``mark(rule)``. Spans
    that overlap an earlier redaction marker (a regex match) are ignored; a street phrase
    wins over a name inside it ("King Abdullah Road")."""
    if not _LETTER.search(text):
        return text
    blocked = [m.span() for m in _MARKER.finditer(text)]
    streets = [
        m.span()
        for pattern in _STREETS
        for m in pattern.finditer(text)
        if not _overlaps(*m.span(), blocked)
    ]
    places = [_Place(start, end, True) for start, end in streets]
    persons: list[tuple[int, int]] = []
    for span in detector.detect(text):
        if not 0 <= span.start < span.end <= len(text):
            continue
        if _overlaps(span.start, span.end, blocked) or _overlaps(span.start, span.end, streets):
            continue
        if span.kind == "person":
            persons.append((span.start, span.end))
        else:
            street = bool(_STREET_WORD.search(text, span.start, span.end))
            places.append(_Place(span.start, span.end, street))

    masked = [(start, end, "person_name") for start, end in persons]
    group: list[_Place] = []
    for place in sorted(places, key=lambda p: (p.start, -p.end)):
        if group and place.start < group[-1].end:
            continue
        if group and not _JOINED.fullmatch(text, group[-1].end, place.start):
            masked.extend(_address(text, group))
            group = []
        group.append(place)
    if group:
        masked.extend(_address(text, group))

    out: list[str] = []
    position = 0
    for start, end, rule in sorted(masked):
        if start < position:
            continue
        out.append(text[position:start])
        out.append(mark(rule))
        position = end
    out.append(text[position:])
    return "".join(out)


def _address(text: str, group: list[_Place]) -> list[tuple[int, int, str]]:
    """The run of adjacent places as one address, widened over a street word or house
    number before it and a postal code (with its city) after it; nothing if no part of it
    looks like an address."""
    start, end = group[0].start, group[-1].end
    before = text[max(0, start - 40) : start]
    lead = _WORD_BEFORE.search(before) or _NUMBER_BEFORE.search(before)
    if lead:
        start -= len(before) - lead.start()
        before = text[max(0, start - 40) : start]
        if word := _WORD_BEFORE.search(before):  # "Building 7, Olaya Street"
            start -= len(before) - word.start()
    street = any(p.street for p in group)
    postal = (_CITY_POSTAL.match(text, end) if street else None) or _POSTAL_CODE.match(text, end)
    if postal:
        end = postal.end()
    if lead or postal or street:
        return [(start, end, "address")]
    return []
