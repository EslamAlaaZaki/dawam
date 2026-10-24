"""Retrieval quality on the recorded corpus (no real model): recall@k, MRR, citations."""

from __future__ import annotations

import pytest

from tests.rag_eval.rag_eval import evaluate, load_cases, load_passages

MIN_RECALL = 0.9
MIN_MRR = 0.9


def test_the_corpus_is_consistent_and_covers_both_languages():
    ids = {p.id for p in load_passages()}
    cases = load_cases()
    assert all(g in ids for c in cases for g in c["gold"])
    assert any(i.startswith("ar/") for i in ids) and any(i.startswith("en/") for i in ids)


def test_retrieval_scores_meet_the_bar(record_property: pytest.RecordProperty, capsys):
    scores = evaluate(k=3)
    for name, value in scores.items():
        record_property(name, round(value, 3))
    with capsys.disabled():
        print("\nRAG eval: " + ", ".join(f"{n}={v:.3f}" for n, v in scores.items()))
    assert scores["recall@3"] >= MIN_RECALL
    assert scores["mrr"] >= MIN_MRR
    assert scores["citation_correctness"] == 1.0
