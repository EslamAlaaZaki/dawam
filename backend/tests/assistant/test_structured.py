from dawam.modules.assistant.internal.structured import cards_block, strip_cards


def test_history_replaces_a_cards_block_with_a_one_line_note():
    answer = "Here are KPIs.\n\n" + cards_block([{"name": "Churn", "rationale": "x" * 500}])

    assert strip_cards(answer) == "Here are KPIs.\n\n[KPI suggestions shown as cards]"


def test_text_without_a_cards_block_is_unchanged():
    assert strip_cards("Plain ```sql\nselect 1\n``` answer") == "Plain ```sql\nselect 1\n``` answer"
