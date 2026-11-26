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

## Update 2026-11-27
- A letter that still fails its checks after one revision now pauses the claim (`pending_letter`, a second `interrupt()`). An adjuster submits the final text with no placeholders, and the claim closes with `edited_by` recorded. Before this, a flagged letter was marked but still reached the claimant.
- Code adds the claim-number reference line when the draft leaves it out, so its absence no longer triggers a revision. Average revisions went from 0.83 to 0.08 on the letter eval, with all checks still at 100%.
- Per-adjuster queue: claims can be assigned. Only the assignee may review or fix the letter, and acting on an unassigned claim assigns it to the actor.

