# RAG evaluation corpus

There is no third-party source: every document in `corpus/` was written for this repository by
its contributors, so there is no source URL to record. They are released under the repository's
Apache-2.0 licence and can be redistributed freely. They describe fictional policies and contain
no personal data about real individuals. The whole set is a few kilobytes, far below the 20 MB
limit. `shipping-guide` and `remote-work` are deliberate distractors that share vocabulary with
other documents. `cases.json` holds each question, its gold passage ids
(`<document>#<section heading>`) and a recorded answer that cites passages. The maintainer may
swap in their own documents: keep the same layout.

The harness calls the plain `Gateway` over `FakeAdapter`, not `MeteredGateway`: reaching the
metered gateway needs `RoleService`, which needs the app and a database (Testcontainers), too
heavy for a pure retrieval-metric test. Metering and budgets are covered by `tests/llm`.
