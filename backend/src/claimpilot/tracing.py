"""Tracing hooks. Langfuse is optional: without keys this is a no-op."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from claimpilot.config import get_settings


@lru_cache
def _langfuse_handler() -> Any | None:
    settings = get_settings()
    if not settings.tracing_enabled:
        return None
    # Imported lazily so tests/CI never touch the Langfuse client.
    import os

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key or "")
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key or "")
    if settings.langfuse_host:
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def get_callbacks() -> list[Any]:
    """LangChain callbacks to attach to every model / graph invocation."""
    handler = _langfuse_handler()
    return [handler] if handler else []
