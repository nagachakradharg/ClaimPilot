"""Claim events: one stream feeding the audit trail and live SSE progress.

Producers: graph node start/finish (LangGraph debug stream), model calls (CallRecord), tool
calls (ToolRecord), human decisions and status changes. Every event is PII-redacted once,
here, before it is stored or streamed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from claimpilot import tracing
from claimpilot.config import get_settings
from claimpilot.db import get_sessionmaker
from claimpilot.llm import CallRecord
from claimpilot.models import AuditEvent
from claimpilot.redact import redact
from claimpilot.tools import ToolRecord


class Event(BaseModel):
    claim_id: str
    kind: str  # node_start | node_end | llm | tool | human | status
    name: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = "system"
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    price = get_settings().model_prices.get(model)
    if input_tokens is None and output_tokens is None:
        return None
    if not price:
        return 0.0  # local models and unpriced models
    return round(((input_tokens or 0) * price[0] + (output_tokens or 0) * price[1]) / 1e6, 6)


class EventHub:
    """In-process pub/sub plus a background writer to the audit table."""

    def __init__(self, persist: bool = True) -> None:
        self.persist = persist
        self._known: dict[str, list[str]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._writes: asyncio.Queue[Event] = asyncio.Queue()
        self._writer: asyncio.Task | None = None

    async def start(self) -> None:
        if self.persist and self._writer is None:
            self._writer = asyncio.create_task(self._write_loop())

    async def stop(self) -> None:
        await self.flush()
        if self._writer:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
            self._writer = None

    async def flush(self) -> None:
        if self._writer:
            await self._writes.join()

    def register_pii(self, claim_id: str, values: list[str]) -> None:
        self._known.setdefault(claim_id, []).extend(values)
        tracing.register_pii(values)  # traces are masked with the same known values

    def publish(self, event: Event) -> None:
        known = self._known.get(event.claim_id, [])
        event = event.model_copy(
            update={
                "data": redact(event.data, known),
                "error": redact(event.error, known) if event.error else None,
            }
        )
        if self.persist:
            self._writes.put_nowait(event)
        for q in list(self._subscribers.get(event.claim_id, ())):
            q.put_nowait(event)

    def on_llm(self, r: CallRecord) -> None:
        if not r.run_id:
            return
        self.publish(Event(
            claim_id=r.run_id, kind="llm", name=r.node, latency_ms=r.latency_ms,
            input_tokens=r.input_tokens, output_tokens=r.output_tokens,
            cost_usd=cost_usd(r.model, r.input_tokens, r.output_tokens), error=r.error,
            data={"model": r.model, "source": r.source, "attempts": r.attempts,
                  "repaired": r.repaired},
        ))  # fmt: skip

    def on_tool(self, r: ToolRecord) -> None:
        if not r.run_id:
            return
        self.publish(Event(
            claim_id=r.run_id, kind="tool", name=r.tool, latency_ms=r.latency_ms,
            error=r.error, data={"arguments": r.arguments},
        ))  # fmt: skip

    def on_graph(self, claim_id: str, namespace: tuple[str, ...], chunk: dict[str, Any]) -> None:
        """LangGraph debug-stream chunk -> node_start / node_end event."""
        if chunk.get("type") not in ("task", "task_result"):
            return
        node = chunk["payload"].get("name", "?")
        path = "/".join([ns.split(":")[0] for ns in namespace] + [node])
        data: dict[str, Any] = {"step": chunk.get("step")}
        if chunk["type"] == "task_result" and chunk["payload"].get("error"):
            data["error"] = str(chunk["payload"]["error"])[:500]
        self.publish(Event(
            claim_id=claim_id, kind="node_start" if chunk["type"] == "task" else "node_end",
            name=path, ts=datetime.fromisoformat(chunk["timestamp"]), data=data,
        ))  # fmt: skip

    def subscribe(self, claim_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[claim_id].add(q)
        return q

    def unsubscribe(self, claim_id: str, q: asyncio.Queue[Event]) -> None:
        self._subscribers[claim_id].discard(q)

    async def _write_loop(self) -> None:
        while True:
            event = await self._writes.get()
            try:
                async with get_sessionmaker()() as session, session.begin():
                    session.add(AuditEvent(**event.model_dump()))
            except Exception as exc:  # never let auditing crash claim processing
                print(f"audit write failed: {type(exc).__name__}: {exc}")
            finally:
                self._writes.task_done()
