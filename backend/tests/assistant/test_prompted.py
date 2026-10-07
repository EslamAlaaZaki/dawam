"""Prompted tool calls: the model's text is untrusted, so it is parsed strictly."""

from __future__ import annotations

from dawam.modules.assistant.internal.prompted import (
    PromptedCall,
    PromptedInvalid,
    parse_reply,
    tools_prompt,
)
from dawam.modules.llm import ToolSpec

LOOKUP = ToolSpec(
    "lookup",
    "Find it.",
    {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
        "additionalProperties": False,
    },
)
TOOLS = {"lookup": LOOKUP}


def parse(text: str):
    return parse_reply(text, TOOLS)


def test_the_prompt_lists_each_tool_with_its_schema_and_the_reply_format():
    prompt = tools_prompt([LOOKUP])

    assert "lookup" in prompt and "Find it." in prompt and '"required"' in prompt
    assert '"tool"' in prompt and '"arguments"' in prompt


def test_a_bare_json_object_is_a_tool_call():
    assert parse('{"tool": "lookup", "arguments": {"id": 7}}') == PromptedCall("lookup", {"id": 7})


def test_a_whole_reply_in_a_code_fence_is_a_tool_call():
    reply = '```json\n{"tool": "lookup", "arguments": {"id": 7}}\n```'
    assert parse(reply) == PromptedCall("lookup", {"id": 7})


def test_prose_is_an_answer_even_when_it_mentions_json():
    assert parse("It is at row 7.") is None
    assert parse('Sure! {"tool": "lookup", "arguments": {"id": 7}}') is None
    assert parse('Use {"tool": "x"} like this.') is None
    assert parse("") is None


def test_malformed_json_is_invalid():
    assert isinstance(parse('{"tool": "lookup", "arguments": {"id": 7'), PromptedInvalid)
    assert isinstance(parse("{not json}"), PromptedInvalid)


def test_trailing_text_or_a_second_object_is_invalid():
    one = '{"tool": "lookup", "arguments": {"id": 7}}'
    assert isinstance(parse(one + " thanks"), PromptedInvalid)
    assert isinstance(parse(one + one), PromptedInvalid)


def test_wrong_shape_is_invalid():
    for reply in (
        '{"arguments": {}}',
        '{"tool": "lookup"}',
        '{"tool": "lookup", "arguments": []}',
        '{"tool": 5, "arguments": {}}',
        '{"tool": "lookup", "arguments": {"id": 7}, "extra": 1}',
    ):
        assert isinstance(parse(reply), PromptedInvalid), reply


def test_an_unknown_tool_is_invalid_and_not_echoed():
    result = parse('{"tool": "drop_everything", "arguments": {}}')

    assert isinstance(result, PromptedInvalid)
    assert "drop_everything" not in result.reason and "lookup" in result.reason


def test_arguments_failing_the_schema_are_invalid_and_values_are_not_echoed():
    wrong_type = parse('{"tool": "lookup", "arguments": {"id": "PAYLOAD"}}')
    unknown = parse('{"tool": "lookup", "arguments": {"id": 1, "sql": "PAYLOAD"}}')
    missing = parse('{"tool": "lookup", "arguments": {}}')

    for result in (wrong_type, unknown, missing):
        assert isinstance(result, PromptedInvalid) and "PAYLOAD" not in result.reason
