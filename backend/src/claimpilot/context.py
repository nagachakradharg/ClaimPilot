"""Per-run context: a run_id that links audit rows, traces and cost."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)


def current_run_id() -> str | None:
    return _run_id.get()


@contextmanager
def run_context(run_id: str | None = None):
    token = _run_id.set(run_id or uuid.uuid4().hex)
    try:
        yield _run_id.get()
    finally:
        _run_id.reset(token)
