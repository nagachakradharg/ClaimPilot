"""Client side of the MCP server: every tool call from the graphs goes through `ToolClient`,
which adds a timeout, a `ToolRecord` per call (audit, run_id) and typed results."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from fastmcp import Client
from pydantic import BaseModel

from claimpilot import tracing
from claimpilot.config import Settings, get_settings
from claimpilot.context import current_run_id

T = TypeVar("T", bound=BaseModel)


@dataclass
class ToolRecord:
    tool: str
    arguments: dict[str, Any]
    run_id: str | None
    latency_ms: float
    error: str | None = None


class ToolCallError(RuntimeError):
    pass


class ToolClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._listeners: list[Callable[[ToolRecord], None]] = []

    def add_listener(self, listener: Callable[[ToolRecord], None]) -> None:
        self._listeners.append(listener)

    def _transport(self):
        if self.settings.mcp_server_url == "inprocess":
            from claimpilot.mcp_server import mcp

            return mcp
        return self.settings.mcp_server_url

    async def list_tools(self) -> list[str]:
        async with Client(self._transport()) as client:
            return [t.name for t in await client.list_tools()]

    async def call(self, tool: str, result_type: type[T], **arguments: Any) -> T:
        start = time.perf_counter()
        record = ToolRecord(tool=tool, arguments=arguments, run_id=current_run_id(), latency_ms=0)
        try:
            # A `tool` observation under the calling graph step, with arguments and result
            with tracing.observe(tool, "tool", input=arguments,
                                 metadata={"transport": "mcp"}) as obs:  # fmt: skip
                result = await self._call(tool, arguments)
                if result.is_error:
                    text = " ".join(getattr(c, "text", "") for c in result.content)
                    obs.update(level="ERROR", status_message=text[:500])
                    raise ToolCallError(f"{tool} failed: {text}")
                obs.update(output=result.structured_content)
            return result_type.model_validate(result.structured_content)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record.latency_ms = round((time.perf_counter() - start) * 1000, 1)
            for listener in self._listeners:
                listener(record)

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        async with Client(self._transport()) as client:
            return await asyncio.wait_for(
                client.call_tool(tool, arguments, raise_on_error=False),
                timeout=self.settings.tool_timeout_s,
            )
