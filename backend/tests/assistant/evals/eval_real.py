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


def answer_rag(model: Gateway) -> list[str]:
    """Judge the model's answers to the RAG questions, given the gold passages."""
    passages = {p.id: p.text for p in load_passages()}
    lines = []
    for case in load_cases():
        quoted = [passages[g] for g in case["gold"]]
        context = "\n\n".join(f"[{g}]\n{t}" for g, t in zip(case["gold"], quoted, strict=True))
        events = model.chat([Message("user", f"{case['question']}\n\n{context}")])
        answer = "".join(e.text for e in events if isinstance(e, TextDelta))
        lines.append(answer)
    return lines


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
        scores = [
            judge(
                judge_model, rubric, c["question"], a, [passages[g] for g in c["gold"]]
            ).normalised
            for c, a in zip(cases, answers, strict=True)
        ]
        print(f"judge {rubric}: {round(sum(scores) / len(scores), 3)} over {len(scores)} answers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
