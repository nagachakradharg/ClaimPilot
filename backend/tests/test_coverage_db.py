"""Integration: seed -> retrieve -> coverage graph against real Postgres, fake LLM.
Skipped automatically when the database is not reachable (e.g. CI without services)."""

from datetime import date

import pytest

from claimpilot.db import get_sessionmaker
from claimpilot.domain import ClaimFacts, CoverageDecision
from claimpilot.graphs.coverage import NODE, run_coverage
from claimpilot.llm import LLMClient, fake_responses
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.usefixtures("seeded_db")]


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
