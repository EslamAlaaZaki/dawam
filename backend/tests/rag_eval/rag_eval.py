"""A small retrieval evaluation harness (spec: AI enhancements, retrieval quality).

The corpus in ``corpus/`` is chunked with the product's own chunker, embedded through the
gateway with ``HashingAdapter`` (a recorded-style fake: deterministic bag-of-words vectors, no
model), ranked by cosine similarity, and scored with recall@k, MRR and citation correctness.
Answers come from the scripted ``FakeAdapter`` replies in ``cases.json``. Faithfulness is not
judged here; that is the manual real-model script, never CI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from dawam.modules.files.internal.chunking import chunk
from dawam.modules.llm import FakeAdapter, Gateway, Message, Reply

HERE = Path(__file__).parent
DIM = 512
_WORD = re.compile(r"\w+")
_CITE = re.compile(r"\[([^\]]+#[^\]]+)\]")
_STOP = {"the", "a", "an", "of", "to", "in", "is", "are", "can", "how", "do", "i", "for", "or",
         "من", "في", "ما", "كم", "كيف", "على", "إلى", "هل", "عن"}  # fmt: skip


def tokens(text: str) -> list[str]:
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub("[إأآ]", "ا", text.lower())  # fold alef variants
    out = []
    for word in _WORD.findall(text):
        if word in _STOP:
            continue
        out.append(word[2:] if word.startswith("ال") and len(word) > 4 else word)
    return out


def vectorise(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in tokens(text):
        vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class HashingAdapter(FakeAdapter):
    """``FakeAdapter`` whose embeddings are deterministic hashed bags of words."""

    def __init__(self) -> None:
        super().__init__(embedding_dimension=DIM)

    def embed(self, model: str, texts):  # type: ignore[override]
        super().embed(model, texts)  # records the call
        return [vectorise(t) for t in texts]


@dataclass(frozen=True)
class Passage:
    id: str
    text: str


def load_passages() -> list[Passage]:
    out = []
    for path in sorted((HERE / "corpus").rglob("*.md")):
        doc = f"{path.parent.name}/{path.stem}"
        out += [Passage(f"{doc}#{c.section}", c.text) for c in chunk(path.read_text("utf-8"))]
    return out


def load_cases() -> list[dict]:
    return json.loads((HERE / "cases.json").read_text("utf-8"))["cases"]


def retrieve(llm: Gateway, passages: list[Passage], question: str, k: int) -> list[str]:
    vectors = llm.embed([p.text for p in passages] + [question])
    query = vectors[-1]
    scored = sorted(
        (
            (sum(a * b for a, b in zip(v, query, strict=True)), p.id)
            for v, p in zip(vectors, passages, strict=False)
        ),
        key=lambda s: -s[0],
    )
    return [pid for _, pid in scored[:k]]


def evaluate(k: int = 3) -> dict[str, float]:
    passages = load_passages()
    cases = load_cases()
    recall = rr = cited_ok = 0.0
    for case in cases:
        gold = set(case["gold"])
        llm = Gateway(HashingAdapter(), model="m")
        hits = retrieve(llm, passages, case["question"], k)
        recall += len(gold & set(hits)) / len(gold)
        rr += next((1 / (i + 1) for i, h in enumerate(hits) if h in gold), 0.0)
        chat = Gateway(FakeAdapter().script(Reply(text=case["answer"])), model="m")
        answer = "".join(
            e.text for e in chat.chat([Message("user", case["question"])]) if hasattr(e, "text")
        )
        cites = _CITE.findall(answer)
        cited_ok += bool(cites) and all(c in hits and c in gold for c in cites)
    n = len(cases)
    return {f"recall@{k}": recall / n, "mrr": rr / n, "citation_correctness": cited_ok / n}
