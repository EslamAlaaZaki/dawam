"""The tool-selection evaluation set: shape of the data, and the runner on recorded fakes.

No model is called: each case's recorded answer is replayed through the gateway by the
scripted ``FakeAdapter``. The score is printed (run with ``-s``) per prompt version. The gate
guards the case data and the runner, not prompt regressions: recordings replay whatever the
prompt says. Measuring a prompt means running the cases against a real model.
"""

from __future__ import annotations

from typing import Any

import pytest

from dawam.modules.assistant.internal.tools import TOOLS
from tests.assistant.evals.tool_selection_eval import cases, current_prompt_version, run

PASS_MARK = 0.85


def test_the_set_has_enough_cases_and_a_third_are_arabic():
    everything = cases()

    assert len(everything) >= 30
    assert sum(1 for c in everything if c["language"] == "ar") * 3 >= len(everything)
    assert len({c["id"] for c in everything}) == len(everything)


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["id"])
def test_an_expectation_names_a_real_tool_and_its_arguments(case: dict[str, Any]):
    tool = case["expected"]["tool"]
    if tool is None:
        assert not case["expected"]["arguments"]
        return
    spec = next((t.spec for t in TOOLS if t.name == tool), None)

    assert spec is not None
    assert set(case["expected"]["arguments"]) <= set(spec.parameters["properties"])


def test_recorded_fakes_score_above_the_pass_mark_and_report_the_prompt_version():
    report = run()

    print(report.summary())
    for r in report.results:
        if not r.right_arguments:
            print(f"  miss: {r.id} chose {r.chosen}")
    assert report.prompt_version == current_prompt_version()
    assert report.prompt_version.startswith("p-")
    assert report.tool_score >= PASS_MARK and report.argument_score >= PASS_MARK


def test_the_runner_tells_a_wrong_tool_from_wrong_arguments():
    base = {
        **cases()[0],
        "expected": {"tool": "search_catalog", "arguments": {"query": "customer"}},
    }

    def outcome(tool: str, arguments: dict[str, Any]) -> tuple[bool, bool]:
        result = run([{**base, "recorded": {"tool": tool, "arguments": arguments}}]).results[0]
        return result.right_tool, result.right_arguments

    assert outcome("get_score", {}) == (False, False)
    assert outcome("search_catalog", {"query": "x"}) == (True, False)
    assert outcome("search_catalog", {"query": "customer", "limit": 5}) == (True, True)
