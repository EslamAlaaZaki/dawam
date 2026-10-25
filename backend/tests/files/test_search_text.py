"""Arabic normalisation and chunking: pure functions, no database."""

from __future__ import annotations

from itertools import pairwise

from dawam.modules.files.internal.arabic import normalise, words
from dawam.modules.files.internal.chunking import MAX_TOKENS, chunk, estimate_tokens


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


def _words(text: str) -> int:
    return len(text.split())


def test_long_text_is_split_into_bounded_passages():
    chunks = chunk("\n\n".join(["word " * 100] * 10) + "\n\n" + "x" * 6001)

    assert len(chunks) > 2
    assert all(estimate_tokens(c.text) <= MAX_TOKENS for c in chunks)
    assert chunk("  \n\n ") == []


def test_passages_respect_the_token_size_of_the_given_counter():
    paragraphs = [" ".join(f"p{p}w{i}" for i in range(30)) for p in range(40)]

    chunks = chunk("\n\n".join(paragraphs), _words, target=100, maximum=150, overlap=10)

    assert len(chunks) > 5
    assert all(_words(c.text) <= 150 for c in chunks)
    assert all(_words(c.text) >= 30 for c in chunks)


def test_a_long_paragraph_is_cut_to_the_maximum_in_tokens():
    chunks = chunk(" ".join(f"w{i}" for i in range(1000)), _words, target=100, maximum=150)

    assert len(chunks) > 5
    assert all(_words(c.text) <= 150 for c in chunks)


def test_neighbouring_passages_of_a_section_overlap():
    paragraphs = [" ".join(f"p{p}w{i}" for i in range(30)) for p in range(10)]

    chunks = chunk("\n\n".join(paragraphs), _words, target=70, maximum=150, overlap=10)

    assert len(chunks) > 2
    for before, after in pairwise(chunks):
        carried = after.text.split("\n\n")[0].split()
        assert 5 <= len(carried) <= 10
        assert before.text.split()[-len(carried) :] == carried


def test_overlap_never_crosses_a_section():
    chunks = chunk("# A\n" + "alpha " * 50 + "\n\n# B\n" + "beta " * 50, _words, overlap=10)

    assert [c.section for c in chunks] == ["A", "B"]
    assert "alpha" not in chunks[1].text


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Cats and stocks point opposite ways."""
    return [[1.0, 0.0] if "cat" in t.lower() else [0.0, 1.0] for t in texts]


def test_every_strategy_keeps_passages_within_the_size_limits():
    text = "# Notes\n" + " ".join(
        f"Sentence number {i} about cat or stock topics." for i in range(300)
    )
    for strategy in ("headings", "recursive", "semantic"):
        chunks = chunk(
            text, _words, target=60, maximum=90, overlap=10, strategy=strategy, embed=_fake_embed
        )
        assert chunks, strategy
        assert all(_words(c.text) <= 90 for c in chunks), strategy


def test_the_recursive_strategy_cuts_long_text_at_sentences_not_mid_sentence():
    sentences = [f"Sentence {i} ends here." for i in range(40)]
    chunks = chunk(
        " ".join(sentences), _words, target=30, maximum=45, overlap=0, strategy="recursive"
    )

    assert len(chunks) > 1
    assert all(c.text.endswith("here.") for c in chunks)
    assert all(c.text.startswith("Sentence") for c in chunks)


def test_the_semantic_strategy_splits_where_similarity_drops():
    text = "Cats purr softly. Cats nap all day. Stocks fell sharply. Stocks rose again."

    chunks = chunk(text, _words, strategy="semantic", embed=_fake_embed)

    assert [c.text for c in chunks] == [
        "Cats purr softly. Cats nap all day.",
        "Stocks fell sharply. Stocks rose again.",
    ]


def test_the_semantic_strategy_without_an_embedder_is_recursive():
    text = "Cats purr softly. Stocks fell sharply."

    assert chunk(text, _words, strategy="semantic") == chunk(text, _words, strategy="recursive")


def test_the_semantic_strategy_falls_back_to_recursive_when_vectors_do_not_match_sentences():
    text = "Cats purr softly. Stocks fell sharply."

    chunks = chunk(text, _words, strategy="semantic", embed=lambda texts: [[1.0, 0.0]])

    assert chunks == chunk(text, _words, strategy="recursive")
