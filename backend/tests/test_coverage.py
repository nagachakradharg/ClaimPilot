from datetime import date

import pytest

from claimpilot.config import DATA_DIR
from claimpilot.domain import (
    Citation,
    ClaimFacts,
    CoverageAssessment,
    CoverageDecision,
    CoveragePart,
)
from claimpilot.evals.coverage import load_cases, score_case
from claimpilot.graphs.coverage import _finalize
from claimpilot.rag import Clause, parse_policy_form

POLICY = {
    "form_id": "PAP-STD-01",
    "status": "active",
    "effective_date": date(2026, 1, 1),
    "expiry_date": date(2026, 12, 31),
    "vehicle_acv": 18000.0,
    "collision_deductible": 500.0,
    "comprehensive_deductible": 250.0,
}
FACTS = ClaimFacts(
    policy_number="CP-1",
    incident_date=date(2026, 3, 1),
    claim_type="collision",
    description="Rear-ended at a light.",
)
COLL = Clause("PAP-STD-01", "COLL-1", "Part D", "Collision", "We will pay for direct loss.")


def _state(assessment, policy=POLICY):
    return {"facts": FACTS, "policy": policy, "clauses": [COLL], "assessment": assessment}


def _assessment(decision, part, clause_id="COLL-1", quote="We will pay for direct loss."):
    return CoverageAssessment(
        decision=decision,
        coverage_part=part,
        citations=[Citation(clause_id=clause_id, quote=quote)],
        reasoning="r",
    )


def test_parse_policy_forms():
    std = parse_policy_form((DATA_DIR / "policies" / "PAP-STD-01.md").read_text())
    ids = {c.clause_id for c in std}
    assert {"GEN-1", "COLL-1", "COMP-1", "EXC-2", "LIM-2"} <= ids
    exc = next(c for c in std if c.clause_id == "EXC-2")
    assert exc.section == "Part D — Exclusions" and "transportation network" in exc.text


def test_parse_rejects_duplicates():
    md = "---\nform_id: X\n---\n# S\n### [A-1] a\ntext\n### [A-1] b\ntext\n"
    with pytest.raises(ValueError, match="duplicate"):
        parse_policy_form(md)


async def test_covered_gets_deductible_and_limit_from_policy():
    r = await _finalize(_state(_assessment("covered", "collision")), None)
    assert r.decision is CoverageDecision.COVERED
    assert (r.deductible, r.limit) == (500.0, 18000.0)
    assert r.flags == []


async def test_hallucinated_clause_id_forces_ambiguous():
    r = await _finalize(_state(_assessment("covered", "collision", clause_id="COLL-9")), None)
    assert r.decision is CoverageDecision.AMBIGUOUS
    assert "invalid_citation:COLL-9" in r.flags
    assert r.deductible is None


async def test_non_verbatim_quote_is_flagged_not_dropped():
    r = await _finalize(_state(_assessment("covered", "collision", quote="paraphrase")), None)
    assert r.decision is CoverageDecision.COVERED
    assert "quote_not_verbatim:COLL-1" in r.flags


async def test_covered_without_matching_coverage_part_is_ambiguous():
    liability = POLICY | {"collision_deductible": None, "comprehensive_deductible": None}
    r = await _finalize(_state(_assessment("covered", "collision"), liability), None)
    assert r.decision is CoverageDecision.AMBIGUOUS
    assert "coverage_part_not_on_policy" in r.flags


async def test_missing_policy_is_ambiguous():
    r = await _finalize({"facts": FACTS, "policy": None}, None)
    assert r.decision is CoverageDecision.AMBIGUOUS and r.flags == ["policy_not_found"]


def test_eval_dataset_is_well_formed():
    cases = load_cases()
    assert len(cases) >= 30
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        ClaimFacts.model_validate(c["facts"])
        exp = c["expected"]
        assert set(exp["must_cite"]) <= set(exp["acceptable_citations"])
        assert exp["decision"] in {d.value for d in CoverageDecision}
        assert exp["coverage_part"] in {p.value for p in CoveragePart}


def test_score_case_citation_rules():
    case = {
        "id": "x",
        "expected": {
            "decision": "not_covered",
            "coverage_part": "none",
            "must_cite": ["EXC-2"],
            "acceptable_citations": ["COLL-1", "EXC-2"],
        },
    }
    result = {
        "decision": "not_covered",
        "coverage_part": "none",
        "flags": [],
        "citations": [{"clause_id": "EXC-2"}, {"clause_id": "COLL-1"}],
    }
    s = score_case(case, result)
    assert s["decision_ok"] and s["citation_ok"] and s["escalated"]
    result["citations"].append({"clause_id": "DUT-1"})  # outside acceptable set
    assert not score_case(case, result)["citation_ok"]
