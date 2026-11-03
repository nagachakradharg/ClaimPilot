"""Database engine. Postgres + pgvector holds claims, users, audit, checkpoints and vectors."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from claimpilot.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def check_database() -> dict[str, object]:
    """Return connectivity + pgvector status without raising (used by /health)."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
            row = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            version = row.scalar_one_or_none()
        return {"ok": True, "pgvector": version}
    except Exception as exc:  # report, don't crash the health check
        return {"ok": False, "error": type(exc).__name__}
