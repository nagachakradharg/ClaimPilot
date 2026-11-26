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


async def test_covered_decision_is_grounded_in_its_insuring_clause():
    """Theft cited only via its condition clause (COMP-3) gains the insuring clause COMP-1."""

    def theft(messages):
        return {
            "reasoning": "stolen vehicle",
            "decision": "covered",
            "coverage_part": "comprehensive",
            "citations": [
                {"clause_id": "COMP-3", "quote": "For theft of your covered auto"},
            ],
        }

    fake_responses.register(NODE, theft)
    facts = ClaimFacts(
        policy_number="CP-100003",
        incident_date=date(2026, 3, 4),
        claim_type="theft",
        description="My car was stolen from the driveway overnight; police report filed.",
    )
    r = await run_coverage(facts, LLMClient(), get_sessionmaker())
    cited = [c.clause_id for c in r.citations]
    assert r.decision is CoverageDecision.COVERED
    assert cited == ["COMP-1", "COMP-3"]
    assert r.citations[0].quote.startswith("We will pay")


async def test_other_parts_insuring_clause_is_dropped():
    from claimpilot.domain import Citation, CoveragePart
    from claimpilot.graphs.coverage import _ground_in_insuring_clause

    cits = [Citation(clause_id="COLL-1", quote="q"), Citation(clause_id="COMP-1", quote="q")]
    out = await _ground_in_insuring_clause(
        cits, CoveragePart.COMPREHENSIVE, "PAP-STD-01", get_sessionmaker()
    )
    assert [c.clause_id for c in out] == ["COMP-1"]
    # liability-only form has no COLL-1/COMP-1: nothing is added
    liab = await _ground_in_insuring_clause(
        [Citation(clause_id="LIAB-1", quote="q")],
        CoveragePart.COLLISION,
        "PAP-LIAB-01",
        get_sessionmaker(),
    )
    assert [c.clause_id for c in liab] == ["LIAB-1"]
