"""Concurrent streams over the real HTTP transport don't block each other (#390).

The gateway's HTTP is blocking, so each stream runs on its own thread (the API sizes the pool
with ``DAWAM_REQUEST_THREADS``); a slow stream must not hold up the others. No real model is
called: the answers come from ``StubLlmServer``.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import UrllibTransport
from dawam.platform.config import Settings
from tests.llm.stub_server import StubLlmServer

STREAMS = 8
DELAY = 0.15
LINES = 5  # the stub sends four chunks and [DONE]


def test_slow_streams_run_side_by_side():
    server = StubLlmServer()
    server.stream_delay = DELAY
    with server.running():
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, api_key=None, timeout_seconds=10, transport=UrllibTransport()
        )

        def one_stream(_: int) -> int:
            return len(list(adapter.chat("m", [], [], stream=True, json_schema=None)))

        started = time.monotonic()
        with ThreadPoolExecutor(STREAMS) as pool:
            counts = list(pool.map(one_stream, range(STREAMS)))
        elapsed = time.monotonic() - started

    assert all(count > 0 for count in counts)
    assert elapsed < DELAY * LINES * STREAMS / 2, f"streams ran one after another ({elapsed:.1f}s)"


def test_the_thread_pool_size_is_a_setting():
    assert Settings.model_fields["request_threads"].default == 200
