"""NER-based masking of names and addresses in saved answers and saved text (#385), with
the NER engine faked at its port (``FakeEntityDetector``)."""

from __future__ import annotations

import pytest

from dawam.modules.llm import Reply
from dawam.platform.pii_entities import FakeEntityDetector, set_entity_detector
from tests.assistant import test_chat
from tests.assistant.test_chat import add_system, ask, base, conversation, tool
from tests.assistant.test_gaps_report import content, files

model = test_chat.model  # the fixture that registers and assigns the assistant's model


@pytest.fixture
def ner():
    set_entity_detector(
        FakeEntityDetector(
            {
                "Sara Lindqvist": "person",
                "Riyadh": "location",
                "عمر الحداد": "person",
                "الرياض": "location",
            }
        )
    )
    yield
    set_entity_detector(None)


def test_names_and_addresses_in_a_saved_answer_are_masked(roles, model, fake_llm, ner):
    client = roles.client("owner")
    cid = conversation(client, roles)
    fake_llm.script(
        Reply(
            text="Sara Lindqvist (12 Olaya Street, Riyadh 12211) and عمر الحداد "
            "في شارع العليا، الرياض wrote to a.b@example.com about Riyadh sales."
        )
    )

    ask(client, roles, cid, "Who owns the customer table?")

    saved = client.get(f"{base(roles)}/conversations/{cid}").json()
    [answer] = [m for m in saved["messages"] if m["role"] == "assistant"]
    assert answer["content"] == (
        "[redacted: person_name] ([redacted: address]) and [redacted: person_name] "
        "في [redacted: address] wrote to [redacted: email] about Riyadh sales."
    )


def test_names_in_a_saved_file_are_masked(roles, model, fake_llm, ner):
    system = add_system(roles)
    call = tool(
        "generate_file",
        system_id=system,
        name="gaps-report.md",
        content="Ask Sara Lindqvist or عمر الحداد about `orders`.\n",
    )
    fake_llm.script(Reply(tool_calls=(call,)), Reply(text="done"))
    owner = roles.client("owner")

    ask(owner, roles, conversation(owner, roles), "Write the gaps report")

    [saved] = files(roles, system)
    assert content(roles, saved) == (
        "Ask [redacted: person_name] or [redacted: person_name] about `orders`.\n"
    )
