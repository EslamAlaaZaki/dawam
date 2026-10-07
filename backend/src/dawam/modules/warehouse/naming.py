"""Naming-convention checks (spec story 96, quality rule "Naming conventions respected").

Pure functions: a name and the Data Warehouse's naming rules in, violations out. No
database and no I/O, so the model editor shows them on the objects and scoring can call
them later. The rules apply to Core and Mart tables and columns; Staging mirrors the
source and is exempt, as are columns DAWAM maintains itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ViolationCode = Literal["case_style", "prefix"]

_KIND_PREFIX = {
    "dimension": "dimension_prefix",
    "fact": "fact_prefix",
    "bridge": "bridge_prefix",
}


class NamingRulesLike(Protocol):
    """What the checks read of the warehouse's ``NamingRules``."""

    case_style: str
    dimension_prefix: str
    fact_prefix: str
    bridge_prefix: str


@dataclass(frozen=True)
class NamingViolation:
    code: ViolationCode
    message: str
    expected: str
    """The prefix, or the case style, the name should follow."""


def _case_violation(name: str, case_style: str) -> NamingViolation | None:
    if case_style == "upper":
        ok, label = name == name.upper(), "upper case"
    else:
        ok, label = name == name.lower(), "lower case"
    if ok:
        return None
    return NamingViolation("case_style", f"The name should be in {label}.", case_style)


def check_table_name(
    rules: NamingRulesLike, *, layer: str, kind: str, name: str
) -> list[NamingViolation]:
    """Violations of a table's name: its case, and the prefix its kind calls for
    (compared regardless of case; an empty prefix asks for nothing)."""
    if layer == "staging":
        return []
    found = [v for v in [_case_violation(name, rules.case_style)] if v]
    field = _KIND_PREFIX.get(kind)
    prefix = getattr(rules, field) if field else ""
    if prefix and not name.lower().startswith(prefix.lower()):
        found.append(
            NamingViolation("prefix", f"A {kind} name should start with {prefix!r}.", prefix)
        )
    return found


def check_column_name(
    rules: NamingRulesLike, *, layer: str, name: str, is_system: bool = False
) -> list[NamingViolation]:
    """Violations of a column's name: its case."""
    if layer == "staging" or is_system:
        return []
    return [v for v in [_case_violation(name, rules.case_style)] if v]
