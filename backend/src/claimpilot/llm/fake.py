"""Fake LLM responses (LLM_MODE=fake). No network, deterministic, used by tests, CI and UI work.

Responses are looked up per graph node:
1. programmatic registrations (`fake_responses.register(...)`), e.g. inside a test;
2. JSON files at `<fixtures_dir>/fake/<node>.json`.

A response may be a dict / str / pydantic model, or a callable `(messages) -> response`
so a fake can vary its answer by input (useful for eval-shaped tests).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

FakeResponse = Any | Callable[[Sequence[BaseMessage]], Any]


class FakeResponseMissing(LookupError):
    pass


class FakeResponses:
    def __init__(self) -> None:
        self._registry: dict[str, FakeResponse] = {}

    def register(self, node: str, response: FakeResponse) -> None:
        self._registry[node] = response

    def clear(self) -> None:
        self._registry.clear()

    def get(self, node: str, messages: Sequence[BaseMessage], fixtures_dir: Path) -> Any:
        if node in self._registry:
            response = self._registry[node]
        else:
            path = fixtures_dir / "fake" / f"{node}.json"
            if not path.exists():
                raise FakeResponseMissing(
                    f"No fake response for node {node!r}. Register one or add {path}."
                )
            response = json.loads(path.read_text())
        return response(messages) if callable(response) else response


fake_responses = FakeResponses()
