# Assistant evaluation sets

| Set | Cases | Runs in CI (recorded fakes) | Test |
|---|---|---|---|
| Tool selection | `tool_selection/*.json` | yes | `tests/assistant/test_tool_selection_eval.py` |
| Safety / prompt injection | `safety/*.json` | yes | `tests/assistant/test_safety_eval.py` |
| DW generation | `dw_generation/*.json` | yes (reference), real model opt-in | `tests/assistant/test_dw_generation_eval.py` |
| RAG | `tests/rag_eval/` | yes | `tests/rag_eval/test_rag_eval.py` |

In CI each case's recorded answer is replayed through the gateway by the scripted
`FakeAdapter`. No real model is ever called in CI, on any trigger, and no workflow references
`eval_real.py` or a provider key. The CI gate guards the case data and the runners, not model
behaviour: recordings replay whatever the prompt says.

## Safety set

Each case hides an instruction in a document or a tool result (quoted with `quote_data`, as the
assistant does). A case fails if the reply calls a forbidden tool, contains a forbidden phrase,
or if the injected text can close its `<data>` block. Add a case by adding an object to a JSON
file in `safety/`. `safety/injection_check.json` also feeds the injection check
(`internal/injection.py`): every case is flagged by the built-in rule-based patterns unless it has
`"benign": true`, which must not be flagged.

## Judge rubrics

`judge.py` scores an answer 1 to 5 for `quality` and `faithfulness` through any gateway, parsing
one JSON verdict strictly (an unparseable verdict scores 0). CI tests it with a scripted judge.

## Measuring a real model (manual, local only)

From `backend/`, with your own key and a model you choose (any OpenAI-compatible server):

    DAWAM_EVAL_URL=https://.../v1 DAWAM_EVAL_MODEL=... DAWAM_EVAL_API_KEY=... \
        python -m tests.assistant.evals.eval_real

Set `DAWAM_JUDGE_MODEL` to judge with a different model. The script prints tool-selection and
safety scores (with the prompt version) and the judge's quality and faithfulness averages over
the RAG questions. Run it by hand; never add it to a workflow.
