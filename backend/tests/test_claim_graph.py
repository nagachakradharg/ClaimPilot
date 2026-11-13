"""End-to-end claim graph against Postgres + in-process MCP server + mock core, fake LLM."""

import asyncio
import io
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from PIL import Image
from pydantic import ValidationError

from claimpilot.checkpoint import claim_serde, open_checkpointer
from claimpilot.db import get_sessionmaker
from claimpilot.domain import ClaimForm
from claimpilot.graphs.claim import ClaimService, ClaimStatus, ReviewDecision
from claimpilot.graphs.letter import Decision
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
    "reasoning": "Collision.",
    "decision": "covered",
    "coverage_part": "collision",
    "citations": [{"clause_id": "COLL-1", "quote": "We will pay for direct and accidental loss"}],
}
APPROVAL_LETTER = (
    "Subject: Claim {CLAIM_NUMBER} approved\n\nDear {CLAIMANT_NAME}, claim {CLAIM_NUMBER} is "
    "approved. After your deductible of {DEDUCTIBLE} we will pay {PAYOUT}. ClaimPilot Claims Team"
)
DENIAL_LETTER = (
    "Subject: Claim {CLAIM_NUMBER}\n\nDear {CLAIMANT_NAME}, we are unable to pay claim "
    "{CLAIM_NUMBER}; it is not covered under your policy. ClaimPilot Claims Team"
)
LOW_RISK = {"issues": [], "risk": 0.05}
PHOTO_OK = {"observations": ["rear damage"], "mismatches": [], "risk": 0.05}


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


def _fakes(extraction=EXTRACTION, coverage=COVERED, photo=PHOTO_OK, letter=APPROVAL_LETTER):
    fake_responses.register("intake_extract", extraction)
    fake_responses.register("coverage_assess", coverage)
    fake_responses.register("fraud_doc_consistency", LOW_RISK)
    fake_responses.register("fraud_photo_match", photo)
    fake_responses.register("letter_writer", letter)
    fake_responses.register(
        "letter_reviewer", {"reasoning": "Matches the record.", "approved": True, "issues": []}
    )


def _service(checkpointer=None):
    llm, tools, calls = LLMClient(), ToolClient(), []
    llm.add_listener(calls.append)
    saver = checkpointer or InMemorySaver(serde=claim_serde())
    return ClaimService(llm, tools, get_sessionmaker(), saver), calls


async def test_happy_path_auto_approves_and_closes_with_letter():
    _fakes()
    svc, calls = _service()
    run = await svc.start(_form(), [_photo()])
    assert run.status is ClaimStatus.CLOSED and run.decision is Decision.AUTO_APPROVED
    assert run.final_payout == run.payout.result.payout
    assert f"${run.final_payout:,.2f}" in run.letter.body  # amount injected by code
    assert not run.letter.needs_adjuster_edit
    assert {c.node for c in calls} >= {"intake_extract", "coverage_assess", "fraud_photo_match"}


async def test_fraud_checks_run_in_parallel(monkeypatch):
    """Fakes answer instantly, so give each model call 50 ms of latency like a real one."""
    original = LLMClient.structured

    async def slow_structured(self, node, schema, messages):
        await asyncio.sleep(0.05)
        return await original(self, node, schema, messages)

    monkeypatch.setattr(LLMClient, "structured", slow_structured)
    _fakes()
    svc, _ = _service()
    run = await svc.start(_form(), [_photo()])
    checks = run.fraud.checks
    assert {c.name for c in checks} == {"doc_consistency", "claim_history", "photo_match"}
    latest_start = max(c.started_at for c in checks)
    earliest_finish = min(c.finished_at for c in checks)
    assert latest_start <= earliest_finish  # all three were running at the same moment


async def test_high_fraud_signal_routes_to_review_never_denies():
    _fakes(photo={"observations": ["no damage visible"], "mismatches": ["no damage"], "risk": 0.9})
    svc, _ = _service()
    run = await svc.start(_form(), [_photo()])
    assert run.status is ClaimStatus.PENDING_REVIEW and run.decision is None
    assert any(r.startswith("fraud_score_") for r in run.review_reasons)
    assert run.letter is None  # nothing sent to the claimant before a human decides


async def test_review_pause_then_approve_resumes_without_rerunning_steps():
    _fakes(photo={"observations": [], "mismatches": ["no damage"], "risk": 0.9})
    svc, calls = _service()
    run = await svc.start(_form(), [_photo()])
    assert run.status is ClaimStatus.PENDING_REVIEW
    before = len(calls)
    run = await svc.resume(run.claim_id, ReviewDecision(action="approve"))
    assert run.status is ClaimStatus.CLOSED and run.decision is Decision.APPROVED
    assert [c.node for c in calls[before:]] == ["letter_writer", "letter_reviewer"]


async def test_deny_requires_reason_and_produces_denial_letter():
    with pytest.raises(ValidationError, match="reason is required"):
        ReviewDecision(action="deny")
    not_covered = COVERED | {
        "decision": "not_covered",
        "coverage_part": "none",
        "citations": [{"clause_id": "EXC-2", "quote": "We will not pay for loss"}],
    }
    _fakes(coverage=not_covered, letter=DENIAL_LETTER)
    svc, _ = _service()
    run = await svc.start(_form(), [_photo()])
    run = await svc.resume(run.claim_id, ReviewDecision(action="deny", reason="Delivery use."))
    assert run.decision is Decision.DENIED and run.final_payout is None
    assert "$" not in run.letter.body and not run.letter.needs_adjuster_edit


async def test_override_sets_payout_and_cannot_resume_twice():
    _fakes(photo={"observations": [], "mismatches": ["x"], "risk": 0.9})
    svc, _ = _service()
    run = await svc.start(_form(), [_photo()])
    review = ReviewDecision(action="override", reason="Shop quote attached.", payout_amount=1500)
    run = await svc.resume(run.claim_id, review)
    assert run.final_payout == 1500 and "$1,500.00" in run.letter.body
    with pytest.raises(ValueError, match="not pending_review"):
        await svc.resume(run.claim_id, ReviewDecision(action="approve"))


async def test_paused_claim_survives_restart_with_postgres_checkpointer():
    """Pause, drop the service + DB connection, resume in a new one."""
    _fakes(photo={"observations": [], "mismatches": ["x"], "risk": 0.9})
    async with open_checkpointer() as saver:
        svc, _ = _service(saver)
        run = await svc.start(_form(), [_photo()])
        claim_id = run.claim_id
    assert run.status is ClaimStatus.PENDING_REVIEW

    async with open_checkpointer() as saver:  # "restarted" process
        svc2, calls = _service(saver)
        assert (await svc2.get(claim_id)).status is ClaimStatus.PENDING_REVIEW
        run = await svc2.resume(claim_id, ReviewDecision(action="approve"))
    assert run.status is ClaimStatus.CLOSED and run.decision is Decision.APPROVED
    assert "intake_extract" not in {c.node for c in calls}


async def test_invalid_submission_never_calls_llm():
    _fakes()
    svc, calls = _service()
    run = await svc.start(_form(policy_number="CP-999999"), [])
    assert run.status is ClaimStatus.INVALID
    assert any("not found" in e for e in run.intake.errors)
    assert calls == [] and run.core_claim_id is None


async def test_cancelled_policy_goes_to_review_and_skips_coverage_llm():
    _fakes()
    svc, calls = _service()
    run = await svc.start(_form(policy_number="CP-100023"), [_photo()])
    assert run.status is ClaimStatus.PENDING_REVIEW
    assert "flag:policy_cancelled" in run.review_reasons
    assert len(run.review_reasons) == len(set(run.review_reasons))
    assert "coverage_assess" not in {c.node for c in calls}


async def test_model_failure_marks_claim_failed_and_keeps_partial_state():
    def boom(_messages):
        raise RuntimeError("model down")

    _fakes()
    fake_responses.register("coverage_assess", boom)
    svc, _ = _service()
    run = await svc.start(_form(), [_photo()])
    assert run.status is ClaimStatus.FAILED and "model down" in run.error
    assert run.intake is not None and run.core_claim_id is not None
