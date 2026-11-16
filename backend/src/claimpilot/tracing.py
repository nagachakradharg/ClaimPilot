"""Tracing hooks. Langfuse is optional: without keys this is a no-op.
Traces pass through the same PII redactor as the audit trail."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from claimpilot.config import get_settings
from claimpilot.redact import redact


def _mask(*, data: Any, **_: Any) -> Any:
    return redact(data)


@lru_cache
def _langfuse_handler() -> Any | None:
    settings = get_settings()
    if not settings.tracing_enabled:
        return None
    # Imported lazily so tests/CI never touch the Langfuse client.
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    Langfuse(  # registers the global client the callback handler uses
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        mask=_mask,
    )
    return CallbackHandler()


def get_callbacks() -> list[Any]:
    """LangChain callbacks to attach to every model / graph invocation."""
    handler = _langfuse_handler()
    return [handler] if handler else []
