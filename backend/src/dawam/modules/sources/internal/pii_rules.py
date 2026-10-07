# ruff: noqa: RUF001
"""PII name rules (spec §6.12): a keyword dictionary in English, Arabic transliteration
and Arabic script, matched by regex on normalised column names.

Pure: it reads no source and no database. A name is normalised by lower-casing it,
dropping every separator (``National_ID`` and ``national-id`` both become ``nationalid``)
and folding the common Arabic spellings (diacritics, tatweel, alef, ya and ta marbuta
variants), so ``رقم_الهوية`` and ``رقم الهويه`` are the same name. The patterns below are
written in that normalised form. The first rule that matches wins, so the more specific
ones come first.

A name match alone has confidence at least ``NAME_CONFIDENCE_FLOOR`` (spec §6.12), which
is what makes a freshly extracted column ``suggested`` and therefore protected.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CATEGORIES = ("direct_identifier", "quasi_identifier", "sensitive", "financial")
"""Direct identifier, quasi-identifier, sensitive/special category, financial."""

NAME_CONFIDENCE_FLOOR = 0.5


@dataclass(frozen=True)
class NameRule:
    id: str
    category: str
    confidence: float
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class NameMatch:
    rule: str
    category: str
    confidence: float
    evidence: str
    """Why it matched; mentions the column name and rule, never any data value."""


_ARABIC_FOLD = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)


def normalise(name: str) -> str:
    """Lower case, no separators, folded Arabic spellings."""
    decomposed = unicodedata.normalize("NFKC", name).casefold()
    kept = [
        ch
        for ch in decomposed
        if (ch.isalnum() or "؀" <= ch <= "ۿ")
        and unicodedata.category(ch) != "Mn"  # Arabic diacritics
        and ch != "ـ"  # tatweel
    ]
    return "".join(kept).translate(_ARABIC_FOLD)


def _rule(rule_id: str, category: str, confidence: float, pattern: str) -> NameRule:
    return NameRule(rule_id, category, confidence, re.compile(pattern))


# Order matters: first match wins. Arabic patterns are written already folded.
_RULES: tuple[NameRule, ...] = (
    # -- direct identifiers ---------------------------------------------------------------
    _rule(
        "national_id",
        "direct_identifier",
        0.9,
        r"national(id|no|num|number)|nationalidnumber|citizen(id|no|number)|saudiid|"
        r"^nin$|^natid$|^nid$|رقمالهويه|الهويهالوطنيه|رقممدني|الرقمالقومي|الرقمالوطني",
    ),
    _rule(
        "iqama",
        "direct_identifier",
        0.9,
        r"iqama|iqamah|igama|residen(t|ce)(id|no|number|permit)|رقمالاقامه|اقامه",
    ),
    _rule("hawiya", "direct_identifier", 0.85, r"hawiya|hawiyah|huwiya|huwiyah|hawwiya|هويه"),
    _rule("passport", "direct_identifier", 0.9, r"passport|jawazsafar|جوازالسفر|رقمجواز"),
    _rule(
        "driver_license",
        "direct_identifier",
        0.8,
        r"driv(er|ing)licen[cs]e|licen[cs]e(no|number)|رخصهالقياده|رخصهقياده",
    ),
    _rule(
        "phone",
        "direct_identifier",
        0.8,
        r"jawal|jawwal|mobile|cellphone|cellular|phone|telephone|^tel$|^tel(no|num|number)$|"
        r"gsm|msisdn|جوال|موبايل|هاتف|تليفون|رقمالتلفون|رقمالجوال",
    ),
    _rule(
        "email",
        "direct_identifier",
        0.9,
        r"e?mail(address|addr)?$|^e?mail|ايميل|البريدالالكتروني|بريدالكتروني",
    ),
    _rule(
        "person_name",
        "direct_identifier",
        0.7,
        r"(first|last|middle|full|family|father|mother|given|maiden)name|surname|"
        r"^(fname|lname|mname)$|^(customer|employee|patient|client|beneficiary)name$|"
        r"الاسمالكامل|الاسمالاول|الاسمالاخير|اسمالعائله|اسمالاب|اسمالام|اسمالعميل|"
        r"اسمالموظف|اسمالمريض|اسمالمستفيد",
    ),
    _rule("ip_address", "direct_identifier", 0.7, r"ipaddress|ipaddr|^ip$|clientip|عنوانip"),
    # -- financial -------------------------------------------------------------------------
    _rule("iban", "financial", 0.9, r"iban|ايبان"),
    _rule(
        "card_number",
        "financial",
        0.85,
        r"(credit|debit|payment|bank)?card(no|num|number)|creditcard|^pan$|ccnum|"
        r"رقمالبطاقه|بطاقهائتمان|رقمالفيزا",
    ),
    _rule(
        "bank_account",
        "financial",
        0.8,
        r"(bank)?acc(ount|t)(no|num|number)|bankaccount|رقمالحساب|حسابمصرفي|حسابالبنكي",
    ),
    _rule("salary", "financial", 0.7, r"salary|salaries|wage|income|راتب|مرتب|الدخل"),
    # -- sensitive / special categories ------------------------------------------------------
    _rule("religion", "sensitive", 0.85, r"religio|^sect$|sectarian|الديانه|الدين|مذهب"),
    _rule(
        "health",
        "sensitive",
        0.8,
        r"diagnos|medical|health|disease|illness|disabilit|bloodtype|bloodgroup|medication|"
        r"فصيلهالدم|التشخيص|تشخيص|الحالهالصحيه|مرض|الصحي|صحيه",
    ),
    _rule(
        "biometric_genetic",
        "sensitive",
        0.85,
        r"biometric|fingerprint|faceid|genetic|^dna$|بصمه|الحمضالنووي",
    ),
    _rule(
        "criminal_political",
        "sensitive",
        0.8,
        r"criminal|conviction|politic|ethnic|^race$|سوابق|انتماءسياسي|عرق",
    ),
    # -- quasi-identifiers -------------------------------------------------------------------
    _rule(
        "birth_date",
        "quasi_identifier",
        0.85,
        r"birth(date|day|year)|dateofbirth|yearofbirth|^dob|^bdate|تاريخالميلاد|"
        r"تاريخميلاد|الميلاد",
    ),
    _rule("birth_place", "quasi_identifier", 0.7, r"birthplace|placeofbirth|مكانالميلاد"),
    _rule(
        "nationality",
        "quasi_identifier",
        0.7,
        r"nationality|citizenship|^jinsiya$|جنسيه",
    ),
    _rule("gender", "quasi_identifier", 0.6, r"gender|^sex$|^jins$|^الجنس$|^جنس$|النوعالاجتماعي"),
    _rule("marital_status", "quasi_identifier", 0.6, r"marital|الحالهالاجتماعيه"),
    _rule(
        "address",
        "quasi_identifier",
        0.7,
        r"address|street|zipcode|postcode|postalcode|^zip$|^unnumber$|nationaladdress|"
        r"العنوان|عنوان|شارع|الرمزالبريدي",
    ),
    _rule(
        "commercial_registration",
        "quasi_identifier",
        0.6,
        r"commercialregist|^cr(no|num|number)$|السجلالتجاري",
    ),
)

RULES: dict[str, NameRule] = {rule.id: rule for rule in _RULES}
"""Every built-in name rule by id."""

CUSTOM_PREFIX = "custom:"
"""A custom rule's id in a finding is this plus its name, so it never clashes with a
built-in rule."""
CUSTOM_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 64
MAX_PATTERN_LENGTH = 200
MIN_CUSTOM_CONFIDENCE = NAME_CONFIDENCE_FLOOR
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d*,\d*\})")


@dataclass(frozen=True)
class CustomRule:
    """An organisation-specific rule of a Workspace: keywords matched on normalised column
    names and/or a regex tested on whole sampled values."""

    name: str
    category: str
    confidence: float
    keywords: tuple[str, ...]
    """Normalised (``normalise``), matched as substrings of a normalised column name."""
    pattern: re.Pattern[str] | None

    @property
    def id(self) -> str:
        return CUSTOM_PREFIX + self.name


@dataclass(frozen=True)
class RuleSet:
    """The rules in force for one Workspace: built-in rules it disabled, and its own."""

    disabled: frozenset[str] = frozenset()
    custom: tuple[CustomRule, ...] = ()


DEFAULT_RULES = RuleSet()


def check_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a custom rule's regex; ``ValueError`` if it is too long, invalid or has a
    nested quantifier (which can take exponential time on a long value)."""
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"The pattern is at most {MAX_PATTERN_LENGTH} characters.")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError("The pattern has a nested quantifier such as (a+)+, which is not allowed.")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"The pattern is not a valid regular expression: {exc}.") from None


def compile_custom_rule(
    name: str,
    *,
    keywords: list[str],
    pattern: str | None,
    category: str,
    confidence: float,
) -> CustomRule:
    """Validate and compile a custom rule; ``ValueError`` (a safe message) if it is bad."""
    if not CUSTOM_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "The name is 1 to 40 lower-case letters, digits and underscores, "
            "starting with a letter."
        )
    if category not in CATEGORIES:
        raise ValueError(f"The category must be one of {', '.join(CATEGORIES)}.")
    if not MIN_CUSTOM_CONFIDENCE <= confidence <= 1:
        raise ValueError(f"The confidence is {MIN_CUSTOM_CONFIDENCE} to 1.")
    if len(keywords) > MAX_KEYWORDS or any(len(k) > MAX_KEYWORD_LENGTH for k in keywords):
        raise ValueError(
            f"At most {MAX_KEYWORDS} keywords of up to {MAX_KEYWORD_LENGTH} characters each."
        )
    normalised = tuple(dict.fromkeys(normalise(k) for k in keywords))
    if "" in normalised:
        raise ValueError("A keyword needs at least one letter or digit.")
    compiled = check_pattern(pattern) if pattern else None
    if not normalised and compiled is None:
        raise ValueError("A rule needs at least one keyword or a pattern.")
    return CustomRule(name, category, confidence, normalised, compiled)


def match_name(name: str, rules: RuleSet = DEFAULT_RULES) -> NameMatch | None:
    """The first rule matching the column ``name``, or ``None``. A Workspace's custom rules
    are tried first, then the built-in ones it has not disabled."""
    normalised = normalise(name)
    if not normalised:
        return None
    for custom in rules.custom:
        if any(keyword in normalised for keyword in custom.keywords):
            return NameMatch(
                rule=custom.id,
                category=custom.category,
                confidence=max(custom.confidence, NAME_CONFIDENCE_FLOOR),
                evidence=f'The column name "{name}" matches the custom rule {custom.name}.',
            )
    for rule in _RULES:
        if rule.id in rules.disabled:
            continue
        if rule.pattern.search(normalised):
            return NameMatch(
                rule=rule.id,
                category=rule.category,
                confidence=max(rule.confidence, NAME_CONFIDENCE_FLOOR),
                evidence=f'The column name "{name}" matches the name rule {rule.id}.',
            )
    return None
