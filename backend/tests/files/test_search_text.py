"""Arabic normalisation and chunking: pure functions, no database."""

from __future__ import annotations

from itertools import pairwise

from dawam.modules.files.internal.arabic import normalise, words
from dawam.modules.files.internal.chunking import MAX_TOKENS, chunk, estimate_tokens


def test_a_passage_never_spans_a_page_and_overlap_does_not_cross_one():
    pieces = chunk("alpha " * 400 + "\f" + "omega " * 400)

    assert {c.page for c in pieces} == {1, 2}
    assert all(("alpha" in c.text) != ("omega" in c.text) for c in pieces)


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


def _table(rows: int) -> str:
    body = (f"| {i} | item{i} |" for i in range(rows))
    return "\n".join(["| id | name |", "| --- | --- |", *body])


def test_a_small_table_is_one_passage_apart_from_prose():
    table = _table(3)

    chunks = chunk(f"before text\n{table}\nafter text")

    assert [c.text for c in chunks] == ["before text", table, "after text"]


def test_a_large_table_splits_by_rows_and_repeats_the_header():
    chunks = chunk(_table(60), _words, target=40, maximum=60)

    assert len(chunks) > 2
    assert all(c.text.splitlines()[:2] == ["| id | name |", "| --- | --- |"] for c in chunks)
    assert all(_words(c.text) <= 60 for c in chunks)
    rows = [line for c in chunks for line in c.text.splitlines()[2:]]
    assert rows == _table(60).splitlines()[2:]


def test_pipes_without_a_rule_row_stay_prose():
    assert len(chunk("| a | b |\n| c | d |")) == 1


def test_a_wide_row_is_cut_so_every_part_stays_within_the_bound():
    wide = "| 1 | " + "word " * 100 + "|"
    table = "\n".join(["| id | name |", "| --- | --- |", wide, "| 2 | b |"])

    chunks = chunk(table, _words, target=40, maximum=60)

    assert len(chunks) > 2
    assert all(_words(c.text) <= 60 for c in chunks)
    assert all(c.text.startswith("| id | name |\n| --- | --- |\n") for c in chunks)


def test_a_table_without_rows_emits_no_header_only_passage():
    assert chunk("| id | name |\n| --- | --- |") == []


def test_an_oversized_header_falls_back_to_prose_within_the_bound():
    header = "| " + " | ".join(f"col{i}" for i in range(80)) + " |"
    table = "\n".join([header, "| --- | --- |", "| 1 | 2 |"])

    chunks = chunk(table, _words, target=40, maximum=60)

    assert chunks
    assert all(_words(c.text) <= 60 for c in chunks)


def test_a_table_kept_whole_in_a_parent_is_cut_into_children_by_whole_rows():
    from dawam.modules.files.internal.parents import chunk_with_parents

    rows = [f"| row{n} | {'value ' * 30}|" for n in range(25)]
    table = "\n".join(["| name | note |", "| --- | --- |", *rows])

    children = chunk_with_parents(f"# Rates\n\n{table}")

    assert len({c.parent for c in children}) == 1  # one parent holds the whole table
    assert len(children) > 1
    for child in children:
        lines = child.chunk.text.splitlines()
        assert lines[:2] == ["| name | note |", "| --- | --- |"]
        assert all(line in rows for line in lines[2:])  # no row is cut


def test_parents_never_span_pages_and_children_keep_the_page():
    from dawam.modules.files.internal.chunking import PAGE_BREAK
    from dawam.modules.files.internal.parents import chunk_with_parents

    pages = PAGE_BREAK.join(f"# Report\n\nfirst words of page {n}\n\nmore text" for n in (1, 2))

    children = chunk_with_parents(pages)

    assert [(c.chunk.page, c.parent) for c in children] == [(1, 0), (2, 1)]
    assert all(f"page {c.chunk.page}" in c.parent_text for c in children)
