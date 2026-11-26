# ClaimPilot: case summary

**One line:** An AI claims assistant that closes simple auto claims straight through and gives adjusters an evidence-backed, auditable recommendation for the rest. It runs at $0 per claim on a local model.

## Problem
Auto claim handlers spend most of their time on routine work: reading the story, looking at photos, finding the right policy clause, pulling claim history and repair prices, working out the deductible, and writing the letter. Most claims are simple; a few are risky. Both get the same slow handling, and a risky claim's evidence is scattered across systems, including a legacy core system that no one wants to change.

## Approach
- **Agents read, code decides.** LLMs do the reading and writing: extraction from text and photos, clause interpretation, consistency checks, letters. Routing, payout arithmetic and letter amounts are deterministic code, so the numbers are always right and every decision can be explained.
- **A human review gate.** A claim is auto-approved only when it is covered, low on fraud risk, under $5,000 and free of flags. Everything else pauses (LangGraph `interrupt`, persisted in Postgres) for an adjuster, who sees the reasons, the quoted clauses, the fraud findings and the payout steps. Deny and override require a reason.
- **Legacy integration without touching the legacy system.** An MCP server is the anti-corruption layer over a mock core claims system with legacy codes, string amounts and injected faults. It retries safely, and claim creation is idempotent.
- **Governance built in.** Roles (claimant, adjuster, auditor) are enforced by the API. There is an ordered audit trail of every step, model call (tokens, latency, cost), tool call and human decision, with PII redacted before anything is stored, streamed or traced.
- **Evals, not vibes.** Held-out eval sets for coverage, photo severity and letters. Prompts were tuned on dev sets and checked once on held-out sets.

## Results (local Gemma 12B, $0 per claim)
| Measure | Result |
|---|---|
| Coverage decision accuracy (held-out) | 100% (target ≥ 80%) |
| Risky claims sent to a human (held-out) | 100%, with 0 false auto-approvals (target ≥ 95%) |
| Photo severity (validation photos) | 92% exact, 100% within one level |
| Letters: correct decision and amount | 100% |
| Claim time, laptop | ~70 s to review gate + ~30 s letter, vs an assumed 45 min manual |

## Risks and how they are handled
| Risk | Mitigation |
|---|---|
| The model is wrong about coverage | It cites the clause. An invalid citation forces "ambiguous", which goes to review. A paraphrased quote is flagged for review. |
| The model misstates money | Amounts are placeholders filled in by code. A reviewer step plus code checks block typed amounts and internal terms. |
| Fraud gets through | Three independent checks are combined so that one strong signal is enough. Threshold breaches always go to a human. |
| PII leaks into logs or traces | Pattern- and value-based redaction happens at a single choke point. Claimants are logged by ID. Tests assert no names or emails in the audit trail. |
| Vendor lock-in or cost | `provider:model` per node; fake / replay / local / hosted tiers; cost per claim measured. |
| Legacy system outages | Retries with backoff and idempotent writes. Failures mark the claim failed and visible, never silently lost. |

**Known gaps:**
- Citation correctness (92% held-out) was measured on a held-out set already seen during prompt work; a fresh labeled set is needed.
- Only one model family has been evaluated; a hosted provider still needs paid keys.
- There is one API replica.

## Next steps
1. Run the eval matrix on a hosted provider and choose a model per node by accuracy, cost and latency.
2. Label a fresh held-out set (coverage and citations) to confirm the numbers on unseen cases.
3. Deploy the written AWS stack (ECS Fargate, RDS + pgvector) behind HTTPS, and move photos to S3.
4. Pilot on a client's historical claims with their adjusters' decisions as labels, measuring straight-through rate and override rate.
