"""The DW generation evaluation set (spec §6.9, story 88).

In the normal run, every case's reference model must be a valid ``generate_dw_schema`` answer
and score full marks against itself, and a careless model must score low, so the yardstick
stays sound. Before a release, the set runs against a real model (marker ``real_model``):

    DAWAM_EVAL_URL=https://.../v1 DAWAM_EVAL_MODEL=... DAWAM_EVAL_API_KEY=... \\
        pytest -m real_model tests/assistant/test_dw_generation_eval.py -s

It drives the same agent loop with the case's inputs behind a stand-in ``generate_dw_schema``
tool (no database), prints each case's score and fails below ``PASS_MARK``.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Sequence
from typing import Any

import pytest

from dawam.modules.assistant.internal.agent import Finished, ToolOutcome, run_agent
from dawam.modules.assistant.internal.prompt import JOB_PROMPT, SYSTEM_PROMPT
from dawam.modules.assistant.internal.tools import (
    GENERATION_NOTE,
    GENERATION_TASK,
    TOOLS,
    _GenerateDwSchema,
)
from dawam.modules.llm import AdapterConfig, Capabilities, Gateway, Message, adapter_for
from tests.assistant.evals.dw_generation_eval import cases, score

PASS_MARK = 0.6
URL = os.environ.get("DAWAM_EVAL_URL", "")
MODEL = os.environ.get("DAWAM_EVAL_MODEL", "")
KEY = os.environ.get("DAWAM_EVAL_API_KEY") or None
CASE_NAMES = [c["name"] for c in cases()]


def by_name(name: str) -> dict[str, Any]:
    return next(c for c in cases() if c["name"] == name)


@pytest.mark.parametrize("name", CASE_NAMES)
def test_a_reference_is_a_valid_answer_and_scores_full_marks(name: str):
    case = by_name(name)

    answer = _GenerateDwSchema.model_validate({"tables": case["reference"]})

    assert answer.tables and len(answer.tables) == len(case["reference"])
    assert score(case["reference"], case["reference"]).overall == 1.0


@pytest.mark.parametrize("name", CASE_NAMES)
def test_a_careless_model_scores_low(name: str):
    case = by_name(name)
    careless = [
        {
            "layer": "core",
            "name": t["name"].replace("dim_", "tbl_").replace("fact_", "tbl_"),
            "kind": "dimension",
            "scd_type": 0,
            "columns": [],
        }
        for t in case["reference"]
    ]

    assert score(case["reference"], careless).overall < PASS_MARK


def test_renamed_tables_still_match():
    case = by_name("retail_sales")
    renamed = copy.deepcopy(case["reference"])
    renamed[0]["name"] = "dim_customers"
    for table in renamed:
        for column in table["columns"]:
            if column.get("references") == "dim_customer":
                column["references"] = "dim_customers"

    assert score(case["reference"], renamed).overall == 1.0


class StandIn:
    """``generate_dw_schema`` without a database: the case's inputs, and the answer kept."""

    def __init__(self, case: dict[str, Any]) -> None:
        self._case = case
        self.answer: list[dict[str, Any]] | None = None
        self._spec = next(t for t in TOOLS if t.name == "generate_dw_schema").spec

    def specs(self) -> Sequence[Any]:
        return [self._spec]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name != "generate_dw_schema":
            return ToolOutcome(f"There is no tool called `{name}`.", "error")
        try:
            args = _GenerateDwSchema.model_validate(arguments)
        except Exception as exc:
            return ToolOutcome(f"Invalid arguments: {exc}", "error")
        if args.tables is None:
            return ToolOutcome(json.dumps({**self._case["inputs"], "note": GENERATION_NOTE}), "ok")
        self.answer = [t.model_dump() for t in args.tables]
        return ToolOutcome(json.dumps({"status": "collected"}), "ok")


@pytest.mark.real_model
@pytest.mark.skipif(not (URL and MODEL), reason="DAWAM_EVAL_URL and DAWAM_EVAL_MODEL are not set")
@pytest.mark.parametrize("name", CASE_NAMES)
def test_a_real_model_comes_close_to_the_reference(name: str):
    case = by_name(name)
    adapter = adapter_for(
        "openai_compatible", AdapterConfig(base_url=URL, api_key=KEY, timeout_seconds=600)
    )
    gateway = Gateway(
        adapter, model=MODEL, capabilities=Capabilities(tool_calling=True, context_window=32_000)
    )
    tools = StandIn(case)

    events = list(
        run_agent(
            gateway,
            tools,
            system=SYSTEM_PROMPT + JOB_PROMPT,
            history=[Message("user", GENERATION_TASK)],
            max_tool_calls=8,
        )
    )

    finished = events[-1]
    assert isinstance(finished, Finished) and tools.answer, getattr(finished, "error_message", "")
    result = score(case["reference"], tools.answer)
    print(f"{name}: {result.overall} {result}")
    assert result.overall >= PASS_MARK
