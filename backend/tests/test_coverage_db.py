"""Integration: seed -> retrieve -> coverage graph against real Postgres, fake LLM.
Skipped automatically when the database is not reachable (e.g. CI without services)."""

from datetime import date

import psycopg
import pytest
from sqlalchemy import text

from claimpilot.config import get_settings
from claimpilot.db import get_engine, get_sessionmaker
from claimpilot.domain import ClaimFacts, CoverageDecision
from claimpilot.embeddings import get_embedder
from claimpilot.graphs.coverage import NODE, run_coverage
from claimpilot.llm import LLMClient, fake_responses

TEST_DB = "claimpilot_test"


def _admin_dsn() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def _ensure_test_db() -> str | None:
    """Create the throwaway test database if Postgres is reachable; never touch the dev DB."""
    dsn = _admin_dsn()
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=2) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
            ).fetchone()
            if not exists:
                conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        return None
    base = get_settings().database_url.rsplit("/", 1)[0]
    return f"{base}/{TEST_DB}"


TEST_URL = _ensure_test_db()
pytestmark = pytest.mark.skipif(TEST_URL is None, reason="Postgres not reachable")


@pytest.fixture(autouse=True)
async def seeded(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    get_settings.cache_clear()
    for cached in (get_engine, get_sessionmaker, get_embedder):
        cached.cache_clear()
    from claimpilot.models import Base
    from claimpilot.seed import seed

    async with get_engine().begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    await seed()
    yield
    await get_engine().dispose()
    for cached in (get_engine, get_sessionmaker, get_embedder):
        cached.cache_clear()


def _first_cited(messages):
    """Fake 'model': cover under whichever clause id appears first in the prompt."""
    text = messages[-1].content
    clause_id = text.split("[", 1)[1].split("]", 1)[0]
    return {
        "decision": "covered",
        "coverage_part": "collision",
        "citations": [{"clause_id": clause_id, "quote": "x"}],
        "reasoning": "fake",
    }


async def test_graph_end_to_end_with_fake_llm():
    fake_responses.register(NODE, _first_cited)
    facts = ClaimFacts(
        policy_number="CP-100003",
        incident_date=date(2026, 3, 4),
        claim_type="collision",
        description="Rear-ended at a red light.",
    )
    r = await run_coverage(facts, LLMClient(), get_sessionmaker())
    assert r.decision is CoverageDecision.COVERED
    assert r.deductible is not None and r.limit is not None
    assert "EXC-2" in r.retrieved_clause_ids  # exclusion sweep always present


async def test_cancelled_policy_skips_llm():
    # No fake registered: reaching the LLM would raise FakeResponseMissing.
    facts = ClaimFacts(
        policy_number="CP-100023",
        incident_date=date(2026, 5, 5),
        claim_type="collision",
        description="Slid on ice.",
    )
    r = await run_coverage(facts, LLMClient(), get_sessionmaker())
    assert r.decision is CoverageDecision.NOT_COVERED
    assert r.flags == ["policy_cancelled"] and r.citations[0].clause_id == "GEN-1"
