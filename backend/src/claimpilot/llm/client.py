"""Single entry point for every model call in ClaimPilot.

Graph nodes never touch a provider SDK directly; they call `LLMClient.structured(...)` or
`LLMClient.text(...)` with their node name. That gives us, in one place:

- mode switching (fake / replay / record / live) from `.env` only
- per-node model selection (`LLM_NODE_MODELS`)
- timeouts + retries with backoff
- structured-output validation with one repair attempt
- a `CallRecord` for every call, for the audit log and cost reporting
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from claimpilot.config import LLMMode, Settings, get_settings
from claimpilot.context import current_run_id
from claimpilot.llm.fake import fake_responses
from claimpilot.llm.models import build_chat_model
from claimpilot.llm.replay import ReplayStore, replay_key
from claimpilot.tracing import callbacks_unless_inherited

T = TypeVar("T", bound=BaseModel)


class LLMCallError(RuntimeError):
    """Raised after retries / repair are exhausted. Callers route the claim to review / FAILED."""


@dataclass
class CallRecord:
    node: str
    model: str
    mode: str
    run_id: str | None
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts: int = 1
    repaired: bool = False
    source: str = "model"  # model | fake | replay
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


CallListener = Callable[[CallRecord], None]


def _usage(msg: AIMessage | None) -> tuple[int | None, int | None]:
    usage = getattr(msg, "usage_metadata", None) or {}
    return usage.get("input_tokens"), usage.get("output_tokens")


# Trace names per model step; dashboards and evaluators key on them, so keep them stable.
GENERATION_NAMES = {
    "intake_extract": "extract-claim-facts",
    "coverage_assess": "assess-coverage",
    "fraud_doc_consistency": "check-document-consistency",
    "fraud_photo_match": "match-photos-to-story",
    "letter_writer": "write-letter",
    "letter_reviewer": "review-letter",
}


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.replay = ReplayStore(self.settings.llm_fixtures_dir)
        self._listeners: list[CallListener] = []

    def add_listener(self, listener: CallListener) -> None:
        self._listeners.append(listener)

    def _emit(self, record: CallRecord) -> None:
        for listener in self._listeners:
            listener(record)

    async def structured(self, node: str, schema: type[T], messages: Sequence[BaseMessage]) -> T:
        """Call the node's model and return a validated `schema` instance."""
        output = await self._call(node, messages, schema)
        return schema.model_validate(output)

    async def text(self, node: str, messages: Sequence[BaseMessage]) -> str:
        output = await self._call(node, messages, None)
        return str(output)

    async def _call(
        self, node: str, messages: Sequence[BaseMessage], schema: type[BaseModel] | None
    ) -> Any:
        spec = self.settings.model_for(node)
        mode = self.settings.llm_mode
        start = time.perf_counter()
        record = CallRecord(
            node=node, model=spec, mode=mode.value, run_id=current_run_id(), latency_ms=0.0
        )
        try:
            if mode is LLMMode.FAKE:
                record.source = "fake"
                output = fake_responses.get(node, messages, self.settings.llm_fixtures_dir)
                if isinstance(output, BaseModel):
                    output = output.model_dump(mode="json")
                if schema is not None:
                    schema.model_validate(output)  # fakes must also satisfy the contract
                return output

            variant = (
                f"{spec}|reasoning={self.settings.llm_reasoning}"
                if self.settings.llm_reasoning
                else spec
            )
            key = replay_key(node, variant, schema.__name__ if schema else None, messages)
            if mode is LLMMode.REPLAY:
                record.source = "replay"
                entry = self.replay.load(node, key)
                record.input_tokens = entry.get("input_tokens")
                record.output_tokens = entry.get("output_tokens")
                return entry["output"]

            output = await self._call_model(node, spec, messages, schema, record)
            if mode is LLMMode.RECORD:
                self.replay.save(
                    node,
                    key,
                    {
                        "node": node,
                        "model": spec,
                        "output": output,
                        "input_tokens": record.input_tokens,
                        "output_tokens": record.output_tokens,
                    },
                )
            return output
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record.latency_ms = round((time.perf_counter() - start) * 1000, 1)
            self._emit(record)

    async def _call_model(
        self,
        node: str,
        spec: str,
        messages: Sequence[BaseMessage],
        schema: type[BaseModel] | None,
        record: CallRecord,
    ) -> Any:
        provider, _, model_name = spec.partition(":")
        model = build_chat_model(spec)  # cached and shared: never mutate it
        name = GENERATION_NAMES.get(node, node)
        model = model.model_copy(update={"name": name}) if hasattr(model, "model_copy") else model
        runnable = model.with_structured_output(schema, include_raw=True) if schema else model
        callbacks = callbacks_unless_inherited()
        config = {
            **({"callbacks": callbacks} if callbacks is not None else {}),
            "tags": [node],
            "metadata": {
                "node": node,
                "run_id": record.run_id,  # links audit rows, cost and traces
                "ls_provider": provider,
                "ls_model_name": model_name,
            },
        }
        convo = list(messages)

        for repair_round in range(2):  # first try + one repair attempt
            result = await self._invoke_with_retries(runnable, convo, config, record)
            if schema is None:
                record.input_tokens, record.output_tokens = _usage(result)
                return result.content

            raw: AIMessage | None = result.get("raw")
            record.input_tokens, record.output_tokens = _usage(raw)
            parsed, error = result.get("parsed"), result.get("parsing_error")
            if parsed is not None and error is None:
                try:
                    return schema.model_validate(parsed).model_dump(mode="json")
                except ValidationError as exc:
                    error = exc
            if repair_round == 0:
                record.repaired = True
                if raw is not None:
                    convo.append(raw)
                convo.append(
                    HumanMessage(
                        f"Your previous answer did not match the required schema: {error}. "
                        "Reply again with output that matches the schema exactly."
                    )
                )
        raise LLMCallError(f"[{node}] structured output invalid after repair: {error}")

    async def _invoke_with_retries(
        self, runnable: Any, convo: list[BaseMessage], config: dict, record: CallRecord
    ) -> Any:
        attempts = self.settings.llm_max_retries + 1
        for attempt in range(1, attempts + 1):
            record.attempts = attempt
            try:
                return await asyncio.wait_for(
                    runnable.ainvoke(convo, config=config), timeout=self.settings.llm_timeout_s
                )
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt == attempts:
                    raise LLMCallError(
                        f"model call failed after {attempts} attempts: {exc}"
                    ) from exc
            except Exception as exc:  # provider SDK errors (rate limit, 5xx) vary by package
                if attempt == attempts or not _is_retryable(exc):
                    raise
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise AssertionError("unreachable")


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(s in name for s in ("ratelimit", "timeout", "connection", "overloaded"))
