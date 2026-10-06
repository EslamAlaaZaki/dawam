"""Arabic normalisation and chunking: pure functions, no database."""

from __future__ import annotations

from dawam.modules.files.internal.arabic import normalise, words
from dawam.modules.files.internal.chunking import MAX_CHARS, chunk


def test_diacritics_are_removed_and_letter_forms_unified():
    assert normalise("مُدَرِّسَة") == normalise("مدرسه")
    assert normalise("أحمد إبراهيم آمن") == "احمد ابراهيم امن"
    assert normalise("مستشفى") == normalise("مستشفي")
    assert normalise("كــتاب") == "كتاب"


def test_words_are_normalised_and_distinct():
    assert words("Customer, CUSTOMER and العَمِيل!") == ["customer", "and", "العميل"]


def test_a_heading_names_the_section_of_its_passages():
    chunks = chunk("intro text\n\n# Ledger\nPostings go here.\n\n## Balances\nDaily.")

    assert [(c.section, c.text) for c in chunks] == [
        ("Part 1", "intro text"),
        ("Ledger", "Postings go here."),
        ("Balances", "Daily."),
    ]


def test_long_text_is_split_into_bounded_passages():
    chunks = chunk("\n\n".join(["word " * 100] * 10) + "\n\n" + "x" * (MAX_CHARS * 2 + 1))

    assert len(chunks) > 2
    assert all(len(c.text) <= MAX_CHARS * 2 for c in chunks)
    assert chunk("  \n\n ") == []
