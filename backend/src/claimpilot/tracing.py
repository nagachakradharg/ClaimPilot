"""Langfuse tracing (https://langfuse.com/docs/observability/best-practices).

- One trace per claim run segment (`process-claim`, `resume-claim-after-review`,
  `finish-claim-after-letter-edit`); human pauses split a claim, so the claim id is the
  session. Evals: one trace per case, one session per run.
- Graph steps, named generations, the clause `retriever` and MCP `tool` calls nest where
  they ran; LangChain plumbing is skipped.
- PII is masked at export: patterns plus known claimant values (names have no pattern).

Without Langfuse keys (or with TRACING=false) every helper is a no-op.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any
from uuid import UUID

from claimpilot import __version__
from claimpilot.config import get_settings
from claimpilot.redact import redact_text

# Known PII is process-wide: the export-time masking hook must not use request-local state.
_KNOWN_MAX = 5000
_known: OrderedDict[str, None] = OrderedDict()
_known_lock = threading.Lock()


def register_pii(values: list[str]) -> None:
    """Values (name parts, email, phone, address) to mask in every exported span."""
    with _known_lock:
        for v in values:
            if v and len(v) >= 3:
                _known[v] = None
                _known.move_to_end(v)
        while len(_known) > _KNOWN_MAX:
            _known.popitem(last=False)


def _known_values() -> tuple[str, ...]:
    with _known_lock:
        return tuple(_known)


def _mask_value(value: Any, known: tuple[str, ...]) -> Any | None:
    """Masked replacement, or None when nothing changed."""
    if isinstance(value, str):
        masked = redact_text(value, known)
        return masked if masked != value else None
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value):
        masked_list = [redact_text(v, known) for v in value]
        return masked_list if masked_list != list(value) else None
    return None


def mask_otel_spans(*, params: Any) -> Any:
    """Export-time masking of every string attribute (inputs, outputs, metadata)."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    known = _known_values()
    patches = {}
    for ident, span in params.spans.items():
        changed = {}
        for key, value in span.attributes.items():
            if (masked := _mask_value(value, known)) is not None:
                changed[key] = masked
        if changed:
            patches[ident] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches)


# MCP SDK spans carry no arguments or results; ToolClient records its own `tool`
# observation with both (see tools.py), so these would only duplicate it, empty.
_DROPPED_SCOPES = ("fastmcp", "mcp-python-sdk")


def should_export_span(span: Any) -> bool:
    from langfuse.span_filter import is_default_export_span

    scope = getattr(span.instrumentation_scope, "name", "") or ""
    return is_default_export_span(span) and not scope.startswith(_DROPPED_SCOPES)


@lru_cache
def get_langfuse() -> Any | None:
    settings = get_settings()
    if not settings.tracing_enabled:
        return None
    # Imported lazily so tests/CI never touch the Langfuse client.
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.env,
        release=__version__,
        mask_otel_spans=mask_otel_spans,
        should_export_span=should_export_span,
    )


def flush() -> None:
    """Send queued spans now (CLI and eval scripts exit right after their work)."""
    if client := get_langfuse():
        client.flush()


# Plumbing LangChain creates around structured output; it says nothing about the claim.
_NOISE_PREFIXES = (
    "RunnableSequence",
    "RunnableParallel",
    "RunnableAssign",
    "RunnableLambda",
    "RunnableWithFallbacks",
    "PydanticOutputParser",
    "JsonOutputParser",
    "PydanticToolsParser",
    "ChannelWrite",
    "<unknown>",  # LangGraph routing/branch internals without a name
)


@lru_cache
def _handler() -> Any | None:
    if get_langfuse() is None:
        return None
    from langfuse.langchain import CallbackHandler

    class ClaimPilotCallbackHandler(CallbackHandler):
        """Skips framework plumbing runs; their children attach to the nearest kept run."""

        def __init__(self) -> None:
            super().__init__()
            self._skipped: dict[UUID, UUID | None] = {}
            self._names: dict[UUID, str] = {}

        def _kept_parent(self, run_id: UUID | None) -> UUID | None:
            seen = 0
            while run_id in self._skipped and seen < 100:
                run_id, seen = self._skipped[run_id], seen + 1
            return run_id

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
            name = self.get_langchain_run_name(serialized, **kw)
            parent = self._kept_parent(parent_run_id)
            # plumbing, or a subgraph directly inside the graph node of the same name
            if parent_run_id is not None and (
                name.startswith(_NOISE_PREFIXES) or self._names.get(parent) == name
            ):
                self._skipped[run_id] = parent_run_id
                return None
            self._names[run_id] = name
            return super().on_chain_start(
                serialized, inputs, run_id=run_id, parent_run_id=parent_run_id, **kw
            )

        def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kw):
            self._names.pop(run_id, None)
            if self._skipped.pop(run_id, False) is not False:
                return None
            return super().on_chain_end(outputs, run_id=run_id, parent_run_id=parent_run_id, **kw)

        def on_chain_error(self, error, *, run_id, parent_run_id=None, **kw):
            self._names.pop(run_id, None)
            if self._skipped.pop(run_id, False) is not False:
                return None
            return super().on_chain_error(error, run_id=run_id, parent_run_id=parent_run_id, **kw)

        def _get_parent_observation(self, parent_run_id):
            return super()._get_parent_observation(self._kept_parent(parent_run_id))

        def current_step_span(self) -> Any | None:
            """OTel span of the LangChain/LangGraph run that is executing right now."""
            from langchain_core.runnables.config import var_child_runnable_config

            config = var_child_runnable_config.get() or {}
            run_id = getattr(config.get("callbacks"), "parent_run_id", None)
            obs = self._runs.get(self._kept_parent(run_id)) if run_id else None
            return getattr(obs, "_otel_span", None)

    return ClaimPilotCallbackHandler()


def get_callbacks() -> list[Any]:
    """LangChain callbacks for a top-level graph or model invocation."""
    handler = _handler()
    return [handler] if handler else []


def callbacks_unless_inherited() -> list[Any] | None:
    """Callbacks for a nested invocation: None when already inside a traced LangChain run.

    Passing an explicit callbacks list would replace the inherited callback manager and cut
    the call loose from its parent step (it would show up at the trace root)."""
    from langchain_core.runnables.config import var_child_runnable_config

    if (var_child_runnable_config.get() or {}).get("callbacks") is not None:
        return None
    return get_callbacks()


@contextmanager
def in_current_step() -> Iterator[None]:
    """Make the running graph step the OTel parent, so non-LangChain spans (MCP tools,
    retrieval) nest under the step that made the call instead of the trace root."""
    handler = _handler()
    span = handler.current_step_span() if handler else None
    if span is None:
        yield
        return
    from opentelemetry import trace

    with trace.use_span(span, end_on_exit=False):
        yield


class _NoopSpan:
    trace_id: str | None = None

    def update(self, **_: Any) -> None:
        pass


@contextmanager
def trace_run(
    name: str,
    *,
    session_id: str,
    user_id: str | None = None,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    environment: str | None = None,
) -> Iterator[Any]:
    """Root observation of one trace, with trace attributes propagated to every child.

    Set the trace output with `span.update(output=...)` before leaving the block.
    """
    client = get_langfuse()
    if client is None:
        yield _NoopSpan()
        return
    from langfuse import propagate_attributes

    attributes = propagate_attributes(
        trace_name=name,
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        metadata={k: str(v) for k, v in (metadata or {}).items()},
        version=__version__,
        **({"environment": environment} if environment else {}),
    )
    with (
        client.start_as_current_observation(as_type="span", name=name, input=input) as root,
        attributes,
    ):
        yield root


def eval_session(suite: str) -> str:
    """One Langfuse session per eval run: every case of the run is grouped under it."""
    from datetime import UTC, datetime

    return f"eval-{suite}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"


@contextmanager
def eval_case(
    suite: str, session_id: str, case_id: str, input: Any, expected: Any
) -> Iterator[Any]:
    """Trace for one eval case; `expected` goes to metadata for LLM-as-a-judge mapping."""
    settings = get_settings()
    with trace_run(
        f"eval-{suite}",
        session_id=session_id,
        input=input,
        metadata={"case_id": case_id, "expected": json.dumps(expected, default=str),
                  "llm_mode": settings.llm_mode.value},
        tags=["eval", suite],
        environment="eval",  # keeps eval traffic out of the app's dashboards
    ) as root:  # fmt: skip
        yield root


@contextmanager
def observe(name: str, as_type: str, input: Any = None, **kwargs: Any) -> Iterator[Any]:
    """A child observation (e.g. `retriever`) under whatever is currently active."""
    client = get_langfuse()
    if client is None:
        yield _NoopSpan()
        return
    with (
        in_current_step(),
        client.start_as_current_observation(
            as_type=as_type, name=name, input=input, **kwargs
        ) as obs,
    ):
        yield obs


def short_json(value: Any, limit: int = 4000) -> Any:
    """Keep large payloads readable in the UI."""
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else text[:limit] + "…"
