"""The rule-based provider of the browser end-to-end suite (``DAWAM_FAKE_LLM``)."""

from __future__ import annotations

import json

from dawam.modules.assistant.internal.tools import GENERATION_TASK, _GenerateDwSchema
from dawam.modules.llm import Message, ToolCall, ToolSpec
from dawam.modules.llm.gateway import TextDelta, ToolCallEvent
from dawam.modules.llm.internal.adapters import AdapterConfig, fake_adapter_factory
from dawam.modules.llm.internal.e2e import E2eAdapter
from dawam.platform.config import Settings

TOOLS = [ToolSpec("generate_dw_schema", "", {"type": "object"})]
INPUTS = json.dumps(
    {"staging": [{"name": "stg_shop_sales_customers"}, {"name": "stg_shop_sales_orders"}]}
)


def calls(*messages: Message) -> list[ToolCall]:
    events = E2eAdapter().chat("m", messages, TOOLS, False, None)
    return [e.call for e in events if isinstance(e, ToolCallEvent)]


def test_the_chat_hands_generation_over_the_job_reads_then_writes_the_model():
    [start] = calls(Message("user", "Generate the core layer"))
    [read] = calls(Message("user", GENERATION_TASK))
    [write] = calls(Message("user", GENERATION_TASK), Message("tool", INPUTS, tool_call_id="read"))

    assert start.arguments == {} and read.arguments == {}
    tables = _GenerateDwSchema.model_validate(write.arguments).tables
    assert [t.name for t in tables] == ["dim_customer", "fact_sales"]


def test_a_tool_result_that_is_not_the_inputs_ends_the_turn_with_text():
    events = list(
        E2eAdapter().chat(
            "m", [Message("user", "x"), Message("tool", "{}", tool_call_id="s")], TOOLS, False, None
        )
    )

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Done."]


def test_the_fake_factory_replaces_every_provider_kind():
    adapter = fake_adapter_factory("openai_compatible", AdapterConfig("http://x", None, 5))

    assert isinstance(adapter, E2eAdapter)


def test_the_setting_is_off_unless_asked_for():
    assert Settings.model_fields["fake_llm"].default is False
