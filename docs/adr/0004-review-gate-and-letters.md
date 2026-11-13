# ADR-0004: Single human review gate with checkpointed interrupt; token-based letters

- Status: Accepted
- Date: 2026-11-26

## Context
Claims decisions need human control, auditability and resilience to restarts (FR-5). Letters must never misstate the decision or amount, or reveal internal fraud details (FR-6).

## Decision
- **One gate.** All §4 triggers are evaluated once, after coverage, fraud and payout: not covered or ambiguous, fraud score ≥ threshold, payout ≥ threshold, low extraction confidence, and any validation flag. The adjuster sees every triggered reason. There are no automatic denials, and a fraud score can only route a claim to review.
- **Interrupt and resume.** The gate pauses at a LangGraph `interrupt()`. State is checkpointed in Postgres with `thread_id = claim_id`. On resume, the adjuster's `ReviewDecision` (approve, deny with a reason, or override with a reason and an amount) continues from the gate without re-running earlier steps. The checkpoint serializer only revives ClaimPilot's own types (explicit allow-list).
- **Coverage and fraud run in parallel**, and the three fraud checks fan out in parallel too. Fraud synthesis is deterministic: a weighted mean, floored at 75% of the strongest single signal.
- **Letters.** The model writes plain text using tokens (`{PAYOUT}`, `{CLAIM_NUMBER}`, ...) that code fills in. Writing long text inside JSON made a local model loop. Review combines deterministic checks (amount tokens, decision wording, banned internal terms) with an LLM check. Standard next steps come from code. There is at most one revision; after that the letter is flagged for adjuster edit.

## Consequences
- The mock core system must persist claims (as a real system of record does) for resume across restarts to work.
- An LLM reviewer can be inconsistent. A rejection without a named problem is ignored, and wording quality still needs a letter eval.
