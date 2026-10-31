"""The ``find_tools`` retrieval set: shape of the data, and the score on the real specs.

No model is called. Run with ``-s`` to see the hit rates per group.
"""

from __future__ import annotations

from typing import Any

import pytest

from dawam.modules.assistant.internal.tools import TOOLS
from tests.assistant.evals.find_tools_eval import cases, run


def test_the_set_has_enough_cases_in_every_group():
    everything = cases()

    assert len(everything) >= 20
    assert len({c["id"] for c in everything}) == len(everything)
    assert {c["group"] for c in everything} == {
        "paraphrase",
        "common_words",
        "exact_name",
        "exact_names",
    }


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["id"])
def test_an_expectation_names_real_tools(case: dict[str, Any]):
    names = {t.name for t in TOOLS}

    assert set(case["expected"]) <= names
    assert case["top1"] is None or case["top1"] in case["expected"]


def test_exact_names_are_found_first_and_the_rates_are_reported():
    report = run()

    print(report.summary())
    for r in report.results:
        if not r.in_top5 or r.first is False:
            print(f"  miss: {r.id} -> {list(r.found)}")
    for group in ("exact_name", "exact_names"):
        assert report.top1(group) == 1.0 and report.top5(group) == 1.0
    # floors at the rates measured for #528: a ranking regression fails here
    assert report.top1("paraphrase") >= 0.846 and report.top5("paraphrase") >= 0.929
    assert report.top1("common_words") >= 0.909 and report.top5("common_words") >= 0.933
