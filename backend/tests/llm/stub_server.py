"""A tiny OpenAI-compatible server on localhost, for tests that exercise the real HTTP
transport end to end. It answers like a small well-behaved model and can be told to fail."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class StubLlmServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_with: int | None = None
        """Answer every request with this HTTP status."""
        self.tools = True
        self.context_window: int | None = 16384
        self.reasoning_field: str | None = None
        """Send a reasoning model's thinking in this message field (vLLM and Ollama:
        ``reasoning``; DeepSeek and older vLLM: ``reasoning_content``)."""
        self.inline_think = False
        """Put the thinking in the content between ``<think>`` tags (Qwen3, DeepSeek-R1)."""
        self.stream_delay = 0.0
        """Seconds a streamed answer waits before each of its lines (a slow model)."""
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self, status: int, body: Any) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                stub.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                entry: dict[str, Any] = {"id": "stub-model", "object": "model"}
                if stub.context_window:
                    entry["max_model_len"] = stub.context_window
                self._reply(200, {"object": "list", "data": [entry]})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "json": body}
                )
                if stub.fail_with:
                    self._reply(stub.fail_with, {"error": {"message": "stub failure"}})
                elif self.path.endswith("/embeddings"):
                    data = [
                        {"index": i, "embedding": [0.5] * 6} for i, _ in enumerate(body["input"])
                    ]
                    self._reply(200, {"object": "list", "data": data})
                elif body.get("stream"):
                    self._stream()
                else:
                    self._reply(200, _thinking(_completion(body, stub.tools), stub))

            def _stream(self) -> None:
                if stub.reasoning_field:
                    first = {stub.reasoning_field: "Let me "}
                    second = {stub.reasoning_field: "think."}
                elif stub.inline_think:
                    first, second = {"content": "<thi"}, {"content": "nk>Let me think.</th"}
                else:
                    first = second = {}
                tail = "ink>" if stub.inline_think else ""
                chunks = [
                    {"choices": [{"index": 0, "delta": first, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": second, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"content": tail}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"content": "O"}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"content": "K"}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                ]
                lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for line in lines:
                    time.sleep(stub.stream_delay)
                    self.wfile.write(line.encode())
                    self.wfile.flush()
                self.close_connection = True

        return Handler

    @contextmanager
    def running(self) -> Iterator[StubLlmServer]:
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self._server.shutdown()
            self._server.server_close()
            thread.join(timeout=5)


def _thinking(reply: dict[str, Any], stub: StubLlmServer) -> dict[str, Any]:
    message = reply["choices"][0]["message"]
    if stub.reasoning_field:
        message[stub.reasoning_field] = "Let me think."
    elif stub.inline_think:
        message["content"] = "<think>Let me think.</think>" + message["content"]
    return reply


def _completion(body: dict[str, Any], tools: bool) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": "OK"}
    finish = "stop"
    if body.get("tools") and tools:
        name = body["tools"][0]["function"]["name"]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": '{"text": "ping"}'},
                }
            ],
        }
        finish = "tool_calls"
    elif body.get("response_format"):
        message["content"] = '{"ok": true}'
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
