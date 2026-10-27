# Supervisor and specialist agents run on our own loop, not LangGraph

The maintainer decided: do not adopt LangGraph. A multi-agent job is built on DAWAM's own tool-use loop (`run_agent`), with the job checkpointing of #401 and the existing LLM gateway (`MeteredGateway`), consistent with the rule that no vendor SDK or LangChain/LangGraph integration is used. Every model call keeps going through the gateway (budgets, internal-only, usage, retries, redaction), and every tool call through `BoundTools.execute`, as the member.

**Shape.** With `DAWAM_ASSISTANT_JOB_SUPERVISOR` on, an assistant job runs as a supervisor. The supervisor is `run_agent` over a small tool set: `write_plan` and `update_plan` (planning, #403), `ask_member` (#402), the two checks self-verification runs (#404), and `delegate`, which hands one complete sub-task to a specialist. The specialists are the source analyst, the DW modeler, the mapper and the KPI analyst. Each is another `run_agent` over the same gateway and the same bound tools, narrowed to its own few tools, with a context of its own: its prompt and the sub-task, nothing of the supervisor's turns. Its report comes back as the `delegate` result, quoted as data. The loop itself is not forked or changed: the supervisor is a `ToolSet`.

**One job, one set of limits.** Specialists' tool calls count against the job's one tool-call cap; a specialist gets what is left, and a `delegate` with nothing left is skipped. Their model calls are metered on the same gateway, so the job's budget covers them, and their tokens are added to the job's totals. A specialist calls only its own tools, and each call is still authorised as the member: a viewer's specialist is refused a write as the single agent is.

**Checkpointing.** The job's checkpoint holds, besides the supervisor's run, the specialist in progress (its own `run_agent` checkpoint, which call it is, and the cap used before it). A job whose worker died resumes the supervisor at the pending `delegate`, and that `delegate` resumes the specialist where it was, without repeating its finished calls. Stored redacted, like the rest of the checkpoint (ADR 0002).

**Verification and questions.** Self-verification runs on the supervisor's run: after it delegated, the job's proposals are checked as a whole and the supervisor can delegate a fix. A specialist does not pause the job: it says in its report what only the member can decide, and the supervisor calls `ask_member`. Parallel reads work inside each run as before; `delegate` is never a read, so specialists run one at a time.

**Off by default.** The flag stays off until the supervisor beats the single-agent loop on the CI fake-based tool-selection evaluation and on one manual `make eval-real` run by the maintainer. With it off, jobs run exactly as before.

## Considered options

- **LangGraph** (or LangChain agents): rejected by the maintainer. It brings its own model clients and state store outside our gateway, budgets and redaction, and is a vendor SDK.
- **Fork `run_agent` into a multi-agent loop**: rejected; two loops would drift. The supervisor is a tool set over the one loop, so checkpointing, planning, verification, `ask_member`, tool search and parallel reads stay in one place.
- **Specialists that ask the member themselves**: not now; a pause inside a nested run would need the question to bubble up through the supervisor's tool call. Letting the supervisor ask keeps one place where a job pauses.
- **Specialists in parallel**: not now; they write into the job's one set of proposals, and running them one at a time keeps every step seeing what the steps before it did.
