"""Manual, local-only: run the eval sets against a real model you choose, with your own key.

    DAWAM_EVAL_URL=https://.../v1 DAWAM_EVAL_MODEL=... DAWAM_EVAL_API_KEY=... \\
        python -m tests.assistant.evals.eval_real

``DAWAM_JUDGE_MODEL`` picks a different model for the judge (default: the same one). It talks
to any OpenAI-compatible ``/chat/completions`` server. Never wired into CI: no workflow runs
it, and no provider key is configured anywhere. Run it from ``backend/``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from dawam.modules.assistant.internal.prompt import quote_data
from dawam.modules.llm import AdapterConfig, Capabilities, Gateway, Message, TextDelta, adapter_for
from tests.assistant.evals import safety_eval, tool_selection_eval
from tests.assistant.evals.judge import RUBRICS, judge
from tests.rag_eval.rag_eval import load_cases, load_passages

USAGE = "Set DAWAM_EVAL_URL and DAWAM_EVAL_MODEL (and DAWAM_EVAL_API_KEY if the server needs one)."


def gateway(env: Mapping[str, str], model_var: str = "DAWAM_EVAL_MODEL") -> Gateway:
    url = env.get("DAWAM_EVAL_URL", "")
    model = env.get(model_var) or env.get("DAWAM_EVAL_MODEL", "")
    if not (url and model):
        raise SystemExit(USAGE)
    adapter = adapter_for(
        "openai_compatible",
        AdapterConfig(base_url=url, api_key=env.get("DAWAM_EVAL_API_KEY") or None),
    )
    return Gateway(
        adapter, model=model, capabilities=Capabilities(tool_calling=True, context_window=32_000)
    )


def passages_for(case: dict, passages: dict[str, str]) -> list[str]:
    """The gold passages plus one distractor from the corpus, as ``[id]`` text pairs."""
    distractor = next(i for i in passages if i not in case["gold"])
    return [f"[{i}]\n{passages[i]}" for i in [*case["gold"], distractor]]


def answer_rag(model: Gateway) -> list[str]:
    """The model's answers to the RAG questions, given quoted passages."""
    passages = {p.id: p.text for p in load_passages()}
    answers = []
    for case in load_cases():
        context = "\n\n".join(quote_data("passage", t) for t in passages_for(case, passages))
        events = model.chat([Message("user", f"{case['question']}\n\n{context}")])
        answers.append("".join(e.text for e in events if isinstance(e, TextDelta)))
    return answers


def main(env: Mapping[str, str] = os.environ) -> int:
    model = gateway(env)
    judge_model = gateway(env, "DAWAM_JUDGE_MODEL")
    print(tool_selection_eval.run(gateway=model).summary())
    safety = safety_eval.run(gateway=model)
    print(safety.summary())
    for r in safety.results:
        if not r.resisted:
            print(f"  followed an injection: {r.id} ({r.reason})")
    cases, answers = load_cases(), answer_rag(model)
    passages = {p.id: p.text for p in load_passages()}
    for rubric in RUBRICS:
        verdicts = [
            judge(judge_model, rubric, c["question"], a, passages_for(c, passages))
            for c, a in zip(cases, answers, strict=True)
        ]
        mean = round(sum(v.normalised for v in verdicts) / len(verdicts), 3)
        bad = sum(1 for v in verdicts if v.score is None)
        print(f"judge {rubric}: {mean} over {len(verdicts)} answers ({bad} unparseable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
