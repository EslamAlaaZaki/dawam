"""The provider behind the browser end-to-end suite (``DAWAM_FAKE_LLM=true``).

A running stack cannot be scripted from a test, so this fake answers by rule instead: asked
to generate the Core Layer it reads the inputs, then models a customer dimension and a sales
fact over whichever staging tables end in ``_customers`` and ``_orders``. Everything else
gets the ``FakeAdapter`` defaults. It never touches the network and is for tests only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from ..gateway import ChatEvent, Message, ToolCall, ToolSpec
from .fake import FakeAdapter, Reply

TOOL = "generate_dw_schema"


def _staging_names(content: str | None) -> dict[str, str]:
    """``{"customers": "stg_shop_sales_customers", ...}`` from the generator's inputs."""
    try:
        # The agent quotes a tool result as `<data source="...">` + JSON + `</data>`.
        text = content or ""
        staging = json.loads(text[text.index("{") : text.rindex("}") + 1])["staging"]
        names = [str(table["name"]) for table in staging]
    except (ValueError, KeyError, TypeError):
        return {}
    return {
        suffix: name
        for name in names
        for suffix in ("customers", "orders")
        if name.endswith(suffix)
    }


def core_model(customers: str, orders: str) -> list[dict[str, Any]]:
    return [
        {
            "layer": "core",
            "name": "dim_customer",
            "kind": "dimension",
            "scd_type": 2,
            "is_conformed": True,
            "description": "One row per customer version.",
            "columns": [
                {
                    "name": "customer_no",
                    "data_type": {"type": "string", "length": 20},
                    "role": "nk",
                },
                {
                    "name": "name",
                    "data_type": {"type": "string", "length": 200},
                    "role": "attribute",
                },
                {
                    "name": "city",
                    "data_type": {"type": "string", "length": 100},
                    "role": "attribute",
                },
            ],
        },
        {
            "layer": "core",
            "name": "fact_sales",
            "kind": "fact",
            "grain": "One row per order.",
            "fact_type": "transactional",
            "columns": [
                {
                    "name": "customer_key",
                    "data_type": {"type": "bigint"},
                    "role": "fk",
                    "references": "dim_customer",
                    "lookup": {
                        "nk_inputs": [f"{customers}.customer_no"],
                        "as_of_input": f"{orders}.order_date",
                    },
                },
                {
                    "name": "order_date_key",
                    "data_type": {"type": "integer"},
                    "role": "fk",
                    "references": "date",
                    "role_name": "order_date",
                },
                {
                    "name": "order_no",
                    "data_type": {"type": "string", "length": 20},
                    "role": "degenerate_dimension",
                },
                {
                    "name": "amount",
                    "data_type": {"type": "decimal", "precision": 18, "scale": 2},
                    "role": "measure",
                    "additivity": "additive",
                },
            ],
        },
    ]


class E2eAdapter(FakeAdapter):
    def chat(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        stream: bool,
        json_schema: dict[str, Any] | None,
    ) -> Iterator[ChatEvent]:
        reply = self._reply(messages, tools)
        if reply is not None:
            self.script(reply)
        return super().chat(model, messages, tools, stream, json_schema)

    @staticmethod
    def _reply(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Reply | None:
        if not messages or TOOL not in {t.name for t in tools}:
            return None
        last = messages[-1]
        if last.role == "user":
            text = (last.content or "").lower()
            if "without `tables`" in text:  # the job: read the inputs first
                return Reply(tool_calls=(ToolCall("read", TOOL, {}),))
            if "generate" in text:  # the chat: hand over to the job
                return Reply(tool_calls=(ToolCall("start", TOOL, {}),))
            return None
        if last.role == "tool":
            names = _staging_names(last.content)
            if len(names) == 2:
                tables = core_model(names["customers"], names["orders"])
                return Reply(tool_calls=(ToolCall("write", TOOL, {"tables": tables}),))
            return Reply(text="Done.")
        return None
