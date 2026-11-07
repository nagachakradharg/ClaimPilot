"""End-to-end claim graph against Postgres + in-process MCP server + mock core, fake LLM."""

import io
from datetime import date

import pytest
from PIL import Image

from claimpilot.db import get_sessionmaker
from claimpilot.domain import ClaimForm
from claimpilot.graphs.claim import ClaimStatus, run_claim
from claimpilot.llm import LLMClient, fake_responses
from claimpilot.tools import ToolClient
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.usefixtures("seeded_db")]

EXTRACTION = {
    "incident_date": "2026-03-04",
    "location": "Austin",
    "vehicle": {"make": None, "model": None, "color": None},
    "damage_areas": ["rear bumper", "trunk"],
    "described_cause": "Rear-ended.",
    "claimed_amount": None,
    "claim_type": "collision",
    "severity": "medium",
    "confidence": {
        k: 0.9
        for k in ("incident_date", "location", "vehicle", "damage_areas", "cause")
        + ("claim_type", "severity")
    },
}
COVERED = {
    "decision": "covered",
    "coverage_part": "collision",
    "citations": [{"clause_id": "COLL-1", "quote": "We will pay for direct and accidental loss"}],
    "reasoning": "Collision.",
}


def _photo() -> tuple[str, bytes]:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 120, 120)).save(buf, format="JPEG")
    return ("p.jpg", buf.getvalue())


def _form(**kw) -> ClaimForm:
    base = {
        "policy_number": "CP-100003",
        "incident_date": date(2026, 3, 4),
        "incident_location": "Austin, TX",
        "description": "Rear-ended at a red light, bumper pushed in.",
    }
    return ClaimForm(**(base | kw))


async def _run(form, photos, extraction=EXTRACTION, coverage=COVERED):
    fake_responses.register("intake_extract", extraction)
    fake_responses.register("coverage_assess", coverage)
    llm, tools, calls, tool_calls = LLMClient(), ToolClient(), [], []
    llm.add_listener(calls.append)
    tools.add_listener(tool_calls.append)
    run = await run_claim(form, photos, llm, tools, get_sessionmaker())
    return run, calls, tool_calls


async def test_happy_path_auto_approves_with_deterministic_payout():
    run, calls, tool_calls = await _run(_form(), [_photo()])
    assert run.status is ClaimStatus.AUTO_APPROVED, run.review_reasons
    assert run.core_claim_id.startswith("CCS-")
    p = run.payout.result
    assert p.payout == round(p.repair_estimate - p.deductible_applied, 2)
    assert [c.node for c in calls] == ["intake_extract", "coverage_assess"]
    assert {c.run_id for c in calls} | {t.run_id for t in tool_calls} == {run.claim_id}


async def test_invalid_submission_never_calls_llm():
    run, calls, _ = await _run(_form(policy_number="CP-999999"), [])
    assert run.status is ClaimStatus.INVALID
    assert any("not found" in e for e in run.intake.errors)
    assert any("photos" in e for e in run.intake.errors)
    assert calls == [] and run.core_claim_id is None


async def test_bad_upload_is_rejected_before_llm():
    run, calls, _ = await _run(_form(), [("x.gif", b"GIF89a....")])
    assert run.status is ClaimStatus.INVALID and calls == []


async def test_not_covered_goes_to_review_without_payout():
    not_covered = COVERED | {
        "decision": "not_covered",
        "coverage_part": "none",
        "citations": [{"clause_id": "EXC-2", "quote": "We will not pay for loss"}],
    }
    run, _, _ = await _run(_form(), [_photo()], coverage=not_covered)
    assert run.status is ClaimStatus.PENDING_REVIEW
    assert run.payout is None and "coverage_not_covered" in run.review_reasons


async def test_low_confidence_and_high_payout_both_listed():
    ex = EXTRACTION | {
        "severity": "high",
        "damage_areas": ["rear bumper", "trunk", "roof", "hood", "left front door", "dashboard"],
        "confidence": EXTRACTION["confidence"] | {"severity": 0.4},
    }
    run, _, _ = await _run(_form(), [_photo()], extraction=ex)
    assert run.status is ClaimStatus.PENDING_REVIEW
    assert "low_confidence:severity" in run.review_reasons
    assert any(r.startswith("payout_at_or_above") for r in run.review_reasons)


async def test_cancelled_policy_flags_and_skips_coverage_llm():
    run, calls, _ = await _run(_form(policy_number="CP-100023"), [_photo()])
    assert run.status is ClaimStatus.PENDING_REVIEW
    assert "flag:policy_cancelled" in run.review_reasons
    assert [c.node for c in calls] == ["intake_extract"]


async def test_model_failure_marks_claim_failed_and_keeps_partial_state():
    def boom(_messages):
        raise RuntimeError("model down")

    fake_responses.register("intake_extract", EXTRACTION)
    fake_responses.register("coverage_assess", boom)
    run = await run_claim(_form(), [_photo()], LLMClient(), ToolClient(), get_sessionmaker())
    assert run.status is ClaimStatus.FAILED and "model down" in run.error
    assert run.intake is not None and run.core_claim_id is not None
