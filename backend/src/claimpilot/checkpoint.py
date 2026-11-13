"""Postgres checkpointer for claim graphs: paused claims survive restarts."""

from __future__ import annotations

import dataclasses
import enum
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from claimpilot.config import get_settings


@cache
def claim_serde() -> JsonPlusSerializer:
    """Serializer that may only revive ClaimPilot's own state types (explicit allow-list)."""
    from claimpilot import domain, estimates, mcp_server, payout, photos
    from claimpilot.graphs import claim, coverage, fraud, intake, letter

    types = [
        obj
        for mod in (
            domain,
            estimates,
            mcp_server,
            payout,
            photos,
            claim,
            coverage,
            fraud,
            intake,
            letter,
        )
        for _, obj in inspect.getmembers(mod, inspect.isclass)
        if obj.__module__ == mod.__name__
        and (issubclass(obj, BaseModel | enum.Enum) or dataclasses.is_dataclass(obj))
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=types)


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(_dsn(), serde=claim_serde()) as saver:
        await saver.setup()  # idempotent; creates checkpoint tables on first use
        yield saver
