"""Routing of chat turns between the light and the agent model (ticket #406)."""

from __future__ import annotations

import pytest

from dawam.modules.assistant.internal.routing import is_simple_question


@pytest.mark.parametrize(
    "text",
    ["What is a KPI?", "How many tables does the sales source have?", "  Who owns orders? "],
)
def test_short_plain_questions_are_simple(text):
    assert is_simple_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Create a staging table for orders",
        "Can you generate a model for orders?",
        "Please update the KPI definition?",
        "Show me the orders table",
        "What is this?\nAnd that?",
        "What is " + "very " * 40 + "long?",
    ],
)
def test_anything_else_goes_to_the_agent_model(text):
    assert not is_simple_question(text)
