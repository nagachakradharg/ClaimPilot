"""Unit tests for fraud rules/synthesis and letter checks (no database)."""

from datetime import UTC, datetime

from claimpilot.domain import ClaimType
from claimpilot.graphs.fraud import CheckResult, history_risk, synthesize_score
from claimpilot.graphs.letter import (
    Decision,
    LetterDraft,
    LetterInput,
    build_letter_graph,
    deterministic_issues,
    parse_draft,
    render,
)
from claimpilot.llm import LLMClient, fake_responses
from claimpilot.mcp_server import ClaimHistory

NOW = datetime.now(UTC)


def _hist(**kw) -> ClaimHistory:
    base = dict(
        policy_number="CP-1",
        past_claims=0,
        days_policy_to_accident=180,
        address_change_recent=False,
        police_report_filed=True,
        witness_present=False,
    )
    return ClaimHistory(**(base | kw))


def _check(name, risk):
    return CheckResult(name=name, risk=risk, findings=[], started_at=NOW, finished_at=NOW)


def test_clean_history_is_zero_risk():
    assert history_risk(_hist(), ClaimType.COLLISION) == (0.0, [])


def test_history_rules_add_up_with_reasons_and_cap():
    risk, why = history_risk(
        _hist(past_claims=5, days_policy_to_accident=3, address_change_recent=True,
              police_report_filed=False),
        ClaimType.THEFT,
    )  # fmt: skip
    assert risk == 1.0 and len(why) == 4
    assert any("police report" in w for w in why)


def test_theft_without_police_report_only_matters_for_theft():
    assert history_risk(_hist(police_report_filed=False), ClaimType.COLLISION)[0] == 0.0


def test_one_strong_signal_reaches_review_threshold():
    score = synthesize_score(
        [_check("doc_consistency", 0.0), _check("claim_history", 0.0), _check("photo_match", 0.9)]
    )
    assert score >= 0.6


def test_all_low_signals_stay_low():
    checks = [_check(n, 0.1) for n in ("doc_consistency", "claim_history", "photo_match")]
    assert synthesize_score(checks) < 0.2


REC = LetterInput(
    decision=Decision.APPROVED,
    claimant_name="Avery Chen",
    claim_number="CCS-2026-000007",
    incident_date="2026-03-04",
    claim_type="collision",
    citations=[],
    reason="Covered under collision.",
    payout=1781.6,
    deductible=400,
)
GOOD = LetterDraft(
    subject="Claim {CLAIM_NUMBER} approved",
    body="Dear {CLAIMANT_NAME}, claim {CLAIM_NUMBER} is approved; we will pay {PAYOUT}.",
)


def test_render_fills_tokens_from_the_record():
    subject, body = render(GOOD, REC)
    assert "CCS-2026-000007" in subject and "$1,781.60" in body and "Avery Chen" in body


def test_good_letter_has_no_issues():
    assert deterministic_issues(GOOD, REC) == []


def test_typed_amounts_and_internal_terms_are_caught():
    bad = LetterDraft(
        subject="Claim {CLAIM_NUMBER}",
        body="Approved. We will pay $1,800 {PAYOUT}. Our fraud check found nothing.",
    )
    issues = deterministic_issues(bad, REC)
    assert any("dollar amount" in i for i in issues)
    assert any("internal review" in i for i in issues)


def test_denial_letter_rules():
    denied = REC.model_copy(update={"decision": Decision.DENIED, "payout": None})
    approved_text = LetterDraft(subject="Claim {CLAIM_NUMBER}", body="We will pay {PAYOUT}.")
    issues = deterministic_issues(approved_text, denied)
    assert any("must not mention a payout" in i for i in issues)
    assert any("does not clearly state the denial" in i for i in issues)


def _as_text(d: LetterDraft) -> str:
    return f"Subject: {d.subject}\n\n{d.body}"


def test_parse_draft_handles_markdown_and_missing_subject():
    d = parse_draft("**Subject:** Claim {CLAIM_NUMBER}\n\nDear {CLAIMANT_NAME},\nHello")
    assert d.subject == "Claim {CLAIM_NUMBER}" and d.body.startswith("Dear")
    assert parse_draft("Just a body").body == "Just a body"


async def test_one_revision_then_flag_for_adjuster(make_settings):
    drafts = iter(
        [
            LetterDraft(subject="Claim", body="We will pay $500."),  # bad
            LetterDraft(subject="Claim", body="We will pay $500 again."),  # still bad
        ]
    )
    fake_responses.register("letter_writer", lambda _m: _as_text(next(drafts)))
    fake_responses.register(
        "letter_reviewer", {"reasoning": "Matches the record.", "approved": True, "issues": []}
    )
    out = await build_letter_graph(LLMClient(make_settings())).ainvoke({"record": REC})
    letter = out["letter"]
    assert letter.revisions == 1 and letter.needs_adjuster_edit and letter.issues


async def test_revision_fixes_letter(make_settings):
    drafts = iter([LetterDraft(subject="Claim", body="We will pay $500."), GOOD])
    fake_responses.register("letter_writer", lambda _m: _as_text(next(drafts)))
    fake_responses.register(
        "letter_reviewer", {"reasoning": "Matches the record.", "approved": True, "issues": []}
    )
    out = await build_letter_graph(LLMClient(make_settings())).ainvoke({"record": REC})
    assert out["letter"].revisions == 1 and not out["letter"].needs_adjuster_edit


def test_deductible_must_be_named_and_absent_when_waived():
    vague = LetterDraft(
        subject="Claim {CLAIM_NUMBER}",
        body="Approved. We will pay {PAYOUT}, less your {DEDUCTIBLE}.",
    )
    assert any("as the deductible" in i for i in deterministic_issues(vague, REC))
    waived = REC.model_copy(update={"deductible": 0})
    uses_token = LetterDraft(
        subject="Claim {CLAIM_NUMBER}", body="Approved: {PAYOUT}. Your deductible is {DEDUCTIBLE}."
    )
    assert any("No deductible applies" in i for i in deterministic_issues(uses_token, waived))


def test_render_adds_claim_reference_when_missing():
    from claimpilot.graphs.letter import LetterDraft, deterministic_issues, render

    draft = LetterDraft(
        subject="Your claim decision", body="Dear {CLAIMANT_NAME}, we will pay {PAYOUT}."
    )
    rec = REC
    assert not any("CLAIM_NUMBER" in i for i in deterministic_issues(draft, rec))
    _, body = render(draft, rec)
    assert body.startswith(f"Claim number: {rec.claim_number}\n")
