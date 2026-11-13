"""Decision-letter subgraph: write -> review -> (one) revise -> review.

The writer drafts from the decision record only, using tokens such as {PAYOUT}; code fills
in the numbers, so the model never types an amount. Review = deterministic
checks (amounts, decision wording, no internal fraud details) + an LLM check (tone, facts).
At most one revision; if it still fails, the letter is flagged for adjuster edit.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from claimpilot.domain import Citation
from claimpilot.llm import LLMClient

WRITER, REVIEWER = "letter_writer", "letter_reviewer"
TOKENS = ("{CLAIMANT_NAME}", "{CLAIM_NUMBER}", "{PAYOUT}", "{DEDUCTIBLE}")
INTERNAL_TERMS = re.compile(
    r"fraud|suspicio|risk score|red flag|investigat|claim history|prior claims", re.I
)

WRITER_PROMPT = """You write decision letters to auto-insurance claimants.
Write a clear, respectful, plain-language letter (120-220 words) based ONLY on the decision
record provided. Structure: greeting, the decision, the reason with the relevant policy
clause(s) quoted, the next steps exactly as given in the record, and a contact line.

Rules:
- Never type dollar amounts, claim numbers or the claimant's name. Use these tokens exactly:
  {CLAIMANT_NAME}, {CLAIM_NUMBER}, {PAYOUT}, {DEDUCTIBLE}. Use {PAYOUT} and {DEDUCTIBLE}
  only when the decision is an approval.
- Do not mention internal review details such as risk checks, investigations or scores.
- Do not promise anything the record does not state.
- Sign off as "ClaimPilot Claims Team".

Reply with the letter only, in this format:
Subject: <one line>

<letter body>
"""

REVIEWER_PROMPT = """You review a claimant decision letter against its decision record.
The letter is expected to: state the decision, give the reason, quote the policy clause(s)
from the record word for word (quoting is required, not a problem), state the next steps
from the record, and end with a contact line. Tokens such as {PAYOUT} have already been
checked by code; ignore formatting and style preferences.

Reject it only for real problems: a decision different from the record, a reason that
contradicts the record, a disrespectful or confusing tone, or a promise or fact that is not
in the record. List each problem; return an empty list if there are none.
"""


class Decision(StrEnum):
    APPROVED = "approved"
    AUTO_APPROVED = "auto_approved"
    DENIED = "denied"


# Standard next steps come from operations policy (code), not from the model.
NEXT_STEPS = {
    Decision.APPROVED: "Payment of {PAYOUT} will be issued within 5 business days.",
    Decision.AUTO_APPROVED: "Payment of {PAYOUT} will be issued within 5 business days.",
    Decision.DENIED: "If you disagree, you may ask for a review by replying to this letter "
    "within 30 days with any additional information.",
}


class LetterInput(BaseModel):
    decision: Decision
    claimant_name: str
    claim_number: str
    incident_date: str
    claim_type: str
    citations: list[Citation]
    reason: str  # claimant-safe reason: coverage reasoning or the adjuster's stated reason
    payout: float | None = None
    deductible: float | None = None


class LetterDraft(BaseModel):
    subject: str
    body: str


def parse_draft(text: str) -> LetterDraft:
    """Letters are generated as plain text (long free text inside JSON can make local
    models loop); the first `Subject:` line is the subject, the rest is the body."""
    text = text.strip().strip("`").strip()
    m = re.match(r"(?is)^\**subject:?\**\s*(.+?)\n(.*)$", text)
    if m:
        return LetterDraft(subject=m.group(1).strip(), body=m.group(2).strip())
    return LetterDraft(subject="Decision on your claim {CLAIM_NUMBER}", body=text)


class LetterReview(BaseModel):
    reasoning: str = Field(description="Check each requirement against the record, briefly")
    approved: bool
    issues: list[str]


class Letter(BaseModel):
    subject: str
    body: str  # rendered, tokens replaced
    revisions: int
    issues: list[str] = Field(default_factory=list)  # unresolved after the revision
    needs_adjuster_edit: bool = False


class LetterState(TypedDict, total=False):
    record: LetterInput
    draft: LetterDraft
    issues: list[str]
    revisions: int
    letter: Letter


def _money(x: float | None) -> str:
    return f"${x:,.2f}" if x is not None else ""


def render(draft: LetterDraft, rec: LetterInput) -> tuple[str, str]:
    values = {
        "{CLAIMANT_NAME}": rec.claimant_name,
        "{CLAIM_NUMBER}": rec.claim_number,
        "{PAYOUT}": _money(rec.payout),
        "{DEDUCTIBLE}": _money(rec.deductible),
    }
    subject = draft.subject
    body = draft.body
    for token, value in values.items():
        subject, body = subject.replace(token, value), body.replace(token, value)
    return subject, body


def deterministic_issues(draft: LetterDraft, rec: LetterInput) -> list[str]:
    issues: list[str] = []
    text = f"{draft.subject}\n{draft.body}"
    approved = rec.decision is not Decision.DENIED
    if re.search(r"\$\s?\d", text):
        issues.append("Typed a dollar amount; use the {PAYOUT}/{DEDUCTIBLE} tokens instead.")
    if approved and "{PAYOUT}" not in text:
        issues.append("Approval letter must state the payout using the {PAYOUT} token.")
    if not approved and ("{PAYOUT}" in text or "{DEDUCTIBLE}" in text):
        issues.append("Denial letter must not mention a payout or deductible.")
    if not rec.deductible and "{DEDUCTIBLE}" in text:
        issues.append("No deductible applies; do not use the {DEDUCTIBLE} token.")
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        bare = sentence.replace("{DEDUCTIBLE}", "").lower()
        if "{DEDUCTIBLE}" in sentence and "deductible" not in bare:
            issues.append("Name the {DEDUCTIBLE} amount as the deductible in the same sentence.")
            break
    if "{CLAIM_NUMBER}" not in text:
        issues.append("Letter must reference the claim using the {CLAIM_NUMBER} token.")
    if approved and re.search(r"\b(denied|deny|not covered)\b", text, re.I):
        issues.append("Approval letter uses denial wording.")
    if not approved and not re.search(r"\b(denied|deny|not covered|unable to)\b", text, re.I):
        issues.append("Denial letter does not clearly state the denial.")
    if m := INTERNAL_TERMS.search(text):
        issues.append(f"Mentions internal review details ('{m.group(0)}'); remove them.")
    if unknown := set(re.findall(r"\{[A-Z_]+\}", text)) - set(TOKENS):
        issues.append(f"Unknown tokens {sorted(unknown)}; use only {', '.join(TOKENS)}.")
    return issues


def _payment_text(rec: LetterInput) -> str:
    if rec.payout is None:
        return "none"
    if rec.deductible:
        return (
            "the net payment to the claimant is {PAYOUT}; the deductible of {DEDUCTIBLE} has "
            "ALREADY been subtracted from it (do not subtract it again)"
        )
    return "the payment to the claimant is {PAYOUT}; no deductible applies to this claim"


def _record_text(rec: LetterInput) -> str:
    clauses = "\n".join(f'- [{c.clause_id}] "{c.quote}"' for c in rec.citations) or "- none"
    return (
        f"Decision: {rec.decision.value}\nClaim type: {rec.claim_type}\n"
        f"Incident date: {rec.incident_date}\nReason: {rec.reason}\n"
        f"Payment: {_payment_text(rec)}\n"
        f"Next steps: {NEXT_STEPS[rec.decision]}\n"
        f"Policy clauses:\n{clauses}"
    )


def build_letter_graph(llm: LLMClient):
    async def write(state: LetterState) -> LetterState:
        rec, revisions = state["record"], state.get("revisions", 0)
        msgs = [
            SystemMessage(WRITER_PROMPT),
            HumanMessage(f"## Decision record\n{_record_text(rec)}"),
        ]
        if "draft" in state:  # revision: show the previous draft and what to fix
            d = state["draft"]
            msgs.append(
                HumanMessage(
                    f"## Previous draft\nSubject: {d.subject}\n{d.body}\n\n## Fix these issues\n"
                    + "\n".join(f"- {i}" for i in state["issues"])
                )
            )
            revisions += 1
        draft = parse_draft(await llm.text(WRITER, msgs))
        return {"draft": draft, "revisions": revisions}

    async def review(state: LetterState) -> LetterState:
        rec, draft = state["record"], state["draft"]
        issues = deterministic_issues(draft, rec)
        verdict = await llm.structured(
            REVIEWER,
            LetterReview,
            [
                SystemMessage(REVIEWER_PROMPT),
                HumanMessage(
                    f"## Decision record\n{_record_text(rec)}\n\n## Letter\n"
                    f"Subject: {draft.subject}\n{draft.body}"
                ),
            ],
        )
        if not verdict.approved and verdict.issues:  # a rejection must name a problem
            issues += verdict.issues
        return {"issues": issues}

    def route(state: LetterState) -> str:
        if state["issues"] and state.get("revisions", 0) < 1:
            return "write"
        return "finalize"

    async def finalize(state: LetterState) -> LetterState:
        subject, body = render(state["draft"], state["record"])
        return {
            "letter": Letter(
                subject=subject,
                body=body,
                revisions=state.get("revisions", 0),
                issues=state["issues"],
                needs_adjuster_edit=bool(state["issues"]),
            )
        }

    g = StateGraph(LetterState)
    g.add_node("write", write)
    g.add_node("review", review)
    g.add_node("finalize", finalize)
    g.add_edge(START, "write")
    g.add_edge("write", "review")
    g.add_conditional_edges("review", route, ["write", "finalize"])
    g.add_edge("finalize", END)
    return g.compile(name="letter")
