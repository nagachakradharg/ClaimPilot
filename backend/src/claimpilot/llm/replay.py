"""Record / replay store (LLM_MODE=record|replay).

Each real response is saved under a key derived from (node, model spec, schema, messages),
so re-running the same input costs $0. Changing a prompt changes the key: re-record it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, messages_to_dict


class ReplayMiss(LookupError):
    pass


def replay_key(
    node: str, model_spec: str, schema_name: str | None, messages: Sequence[BaseMessage]
) -> str:
    payload = json.dumps(
        {
            "node": node,
            "model": model_spec,
            "schema": schema_name,
            "messages": messages_to_dict(list(messages)),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class ReplayStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, node: str, key: str) -> Path:
        return self.root / "replay" / node / f"{key}.json"

    def load(self, node: str, key: str) -> dict[str, Any]:
        path = self._path(node, key)
        if not path.exists():
            raise ReplayMiss(
                f"No recorded response for node {node!r} (key {key}). "
                "Run once with LLM_MODE=record to capture it."
            )
        return json.loads(path.read_text())

    def save(self, node: str, key: str, entry: dict[str, Any]) -> Path:
        path = self._path(node, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2, sort_keys=True, default=str))
        return path
