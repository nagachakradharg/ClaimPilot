# ClaimPilot — Requirements (v1 scope line)

Companion to `claimpilot_project_plan.md`. This file is the source of truth for **what v1 includes**. Anything not listed under "Must" waits until the MVP works end to end.

- **Target role:** Forward-deployed / applied AI engineer
- **Positioning:** An enterprise GenAI system that a client could pilot: measurable business impact, human control, auditability, integration with legacy systems, deployed and evaluated.
- **Line of business:** Personal **auto** claims only (collision, theft, glass, weather).

---

## 1. Goals and success metrics

| Goal | Metric | v1 target |
|---|---|---|
| Correct decisions | Decision accuracy vs labeled set (approve / deny / escalate) | ≥ 80% |
| Safe escalation | Recall on claims that *should* go to a human | ≥ 95% |
| Grounded coverage | Citation correctness (cited clause supports the decision) | ≥ 90% |
| Fraud signal | Precision / recall on labeled fraud cases | Report honestly; no target |
| Speed | p50 end-to-end processing time (excluding human wait) | < 60 s |
| Cost | LLM cost per claim (measured) | Reported per model |
| Business impact | Straight-through rate, est. handling time saved vs stated manual baseline | Shown on dashboard |

Numbers are reported in `evals/report.md`, not asserted.

---

## 2. Scope line

### Must (MVP — above the line)
- Intake with vision: claim form + damage photos → validated structured JSON
- Coverage check via RAG with clause-level citations
- Fraud investigation: 3 parallel checks + synthesis (score + reasons)
- Payout: tool-backed data, **deterministic** calculation, steps shown
- Single human review gate with persisted state (pause / resume)
- Decision letter: writer → reviewer → (one) revision loop
- Parent graph with **deterministic** routing
- Mock legacy "core claims system" API exposed through an MCP server
- FastAPI + SSE live progress
- Audit log of every node, tool call, model call and human decision
- Seeded users for 3 roles (no signup)
- React UI: Submit claim, Adjuster queue, Claim detail, Trace timeline, Impact dashboard
- Provider-agnostic LLM layer; evals run on **≥ 2 providers**
- Eval harness (≥ 30 labeled claims) from week 1
- PII redaction in logs; retries + timeouts on LLM/tool calls
- Docker Compose local setup; cloud deployment with a public demo URL
- Architecture Decision Records, README, 2-minute demo video, 1-page case summary

### Should (stretch — only after MVP ships)
- "Request more info" loop with claimant re-upload and partial re-run
- Prompt-injection checks + adversarial eval cases
- Fallback model on provider failure
- Auditor role pages
- React Flow graph visualization of the trace
- CrewAI implementation of the letter step behind the same interface (comparison)
- Langfuse/LangSmith self-hosted dashboards beyond basic tracing

### Won't (v1)
- Other lines of business (home, health, commercial)
- Self-signup, password reset, user management UI
- Chroma / Qdrant (pgvector only), WebSockets (SSE only)
- Real customer data of any kind
- Automatic denial without a human

---

## 3. Roles and permissions

| Capability | Claimant | Adjuster | Auditor |
|---|---|---|---|
| Submit claim, upload photos | ✅ own | — | — |
| View claim status + decision letter | ✅ own | ✅ all | ✅ all (read-only) |
| View fraud report, citations, payout math | — | ✅ | ✅ |
| Approve / deny / override payout | — | ✅ | — |
| View trace + audit log | — | ✅ own queue | ✅ all |
| View impact dashboard | — | ✅ | ✅ |

Acceptance: every API endpoint enforces role checks server-side; a test exists per role for at least one forbidden action.

---

## 4. Claim lifecycle

```
SUBMITTED → PROCESSING → PENDING_REVIEW → APPROVED → CLOSED
                       ↘ AUTO_APPROVED → CLOSED
                         PENDING_REVIEW → DENIED → CLOSED
                         PENDING_REVIEW → INFO_REQUESTED   (stretch: → PROCESSING)
             PROCESSING → FAILED (after retries; visible to adjuster)
```

### Human review gate — a claim goes to `PENDING_REVIEW` if **any** of:
- Coverage result is **not covered** or **ambiguous** (no auto-denials)
- Fraud risk score ≥ `FRAUD_REVIEW_THRESHOLD` (default 0.6)
- Recommended payout ≥ `PAYOUT_REVIEW_THRESHOLD` (default $5,000)
- Any extracted required field has confidence < `EXTRACTION_CONFIDENCE_MIN` (default 0.7)
- Any workflow step fell back or failed validation

Otherwise → `AUTO_APPROVED`. All thresholds live in config. The adjuster sees **every** triggered reason.

---

## 5. Functional requirements

Each has an ID used in tests, ADRs and commits.

### FR-1 Intake and triage
- FR-1.1 Accept form fields (JSON) + 1–6 photos (JPEG/PNG, ≤ 10 MB each); optional PDF form.
- FR-1.2 Photos are resized before model calls; originals stored on a local volume (object storage in cloud).
- FR-1.3 Model extracts a `ClaimExtraction` schema: incident date, location, vehicle, damage areas, described cause, claimed amount, per-field confidence.
- FR-1.4 Validation: required fields, date within policy period, policy exists and is active.
- FR-1.5 Classify claim type and severity (low / medium / high).
- **Accept:** for the eval set, field-level extraction accuracy is reported; invalid submissions return clear errors without calling the LLM.

### FR-2 Coverage verification (RAG)
- FR-2.1 Policy documents are chunked by clause, embedded, and stored in pgvector with metadata (policy form, section, clause id).
- FR-2.2 Retrieval is filtered to the claimant's policy form.
- FR-2.3 Output: `covered | not_covered | ambiguous`, applicable deductible and limit, and cited clause ids with quoted text.
- FR-2.4 Every cited clause id must exist in the retrieved set (validated in code; otherwise → `ambiguous`).
- **Accept:** citation correctness measured on the eval set; UI shows quoted clauses.

### FR-3 Fraud investigation (parallel)
- FR-3.1 Three checks run concurrently:
  - **Document consistency** (LLM): form vs description vs dates.
  - **Claim history** (rules/SQL): frequency, recent policy start, prior similar claims.
  - **Photo vs description** (vision LLM): damage location/type matches the story.
- FR-3.2 Synthesis node produces score 0–1, per-check findings, and plain-language reasons.
- FR-3.3 A fraud score alone never denies a claim; it can only route to review.
- **Accept:** parallel execution is visible in the trace (overlapping timestamps); precision/recall reported on labeled fraud cases.

### FR-4 Damage assessment and payout
- FR-4.1 MCP server exposes tools: `lookup_policy`, `get_claim_history`, `get_repair_estimates`, `get_vehicle_value`, `create_claim_record`, `update_claim_status` (the last three backed by the mock core claims system).
- FR-4.2 Payout is computed in Python: `min(estimate − deductible, limit)`, with total-loss handling via vehicle value; each step is recorded.
- FR-4.3 The LLM may choose which tools to call and explain the result, but never produces the final number.
- **Accept:** unit tests cover payout math edge cases (below deductible, above limit, total loss).

### FR-5 Human review (interrupt / resume)
- FR-5.1 Graph state is checkpointed in Postgres; a paused claim survives a server restart.
- FR-5.2 Adjuster actions: approve, deny (reason required), override payout (reason required).
- FR-5.3 Resume continues from the gate without re-running earlier steps.
- **Accept:** integration test pauses a claim, restarts the API, resumes, and completes.

### FR-6 Customer communication
- FR-6.1 Writer drafts the letter from final decision, cited clauses and payout steps only.
- FR-6.2 Reviewer checks tone, factual consistency with the decision record, and that no internal fraud details leak to the claimant.
- FR-6.3 At most one revision; if still failing, flag for adjuster edit.
- **Accept:** eval checks letter amount and decision match the record in 100% of cases.

### FR-7 Orchestration
- FR-7.1 Parent graph composes FR-1…FR-6 as subgraphs with conditional edges (no LLM routing).
- FR-7.2 Per-node retry policy and timeout; terminal failure → `FAILED` with reason.
- FR-7.3 Each run has a `run_id` linking audit rows, traces and cost.

### FR-8 API
- FR-8.1 REST: auth, claims CRUD (role-scoped), review actions, audit query, metrics.
- FR-8.2 SSE stream per claim emitting node start/finish events.
- FR-8.3 OpenAPI docs available.

### FR-9 Frontend (React + TypeScript, Vite)
- FR-9.1 Submit claim (form + photo upload, live progress).
- FR-9.2 Adjuster queue (filter by status, reason badges).
- FR-9.3 Claim detail: extraction, coverage with quoted clauses, fraud reasons, payout steps, letter, actions.
- FR-9.4 Trace timeline: per-node duration, tokens, cost, tool calls.
- FR-9.5 Impact dashboard: claims processed, straight-through rate, escalation rate by reason, p50/p95 time, cost per claim, est. handling hours saved (baseline stated on page).
- FR-9.6 Persistent notice: "Synthetic demo. Recommendations only; an adjuster makes final decisions."

---

## 6. Non-functional requirements

| ID | Requirement | Acceptance |
|---|---|---|
| NFR-1 | Audit log: every node, tool call, model call (model, tokens, latency, cost) and human action | Query by `claim_id` returns complete ordered history |
| NFR-2 | PII redaction in logs/traces (names, phone, email, addresses, policy numbers partially masked) | Unit tests on redactor; spot-check traces |
| NFR-3 | Reliability: retries with backoff, per-call timeouts, structured-output validation with one repair attempt | Fault-injection test passes |
| NFR-4 | Provider-agnostic models: provider + model set per node in config | Same eval run on ≥ 2 providers by changing `.env` only |
| NFR-5 | Cost control: response caching in dev, prompt caching where supported, image resizing, spend cap at provider | Cost per claim reported |
| NFR-6 | Security: JWT auth (PyJWT), hashed passwords (pwdlib), secrets via env/secret manager, CORS locked to frontend origin | No secrets in repo; role tests pass |
| NFR-7 | Reproducibility: `docker compose up` + seed script brings up a working demo | ≤ 5 commands in README |
| NFR-8 | Observability: tracing (Langfuse or LangSmith) from week 1 | Every eval run has linked traces |
| NFR-9 | Testability: fake LLM mode for unit/UI tests (no API calls) | CI runs without API keys |

---

## 7. Model strategy (provider-agnostic)

- LLM access via LangChain's `init_chat_model`; config chooses provider + model **per node**.
- Required capabilities: image input, structured output, tool calling.
- **Dev default:** a low-cost multimodal tier. **Eval/demo:** a stronger tier.
- **Eval matrix:** run the full eval on ≥ 2 providers and report accuracy, cost/claim and latency side by side. Choosing a model per step based on this table is a deliverable (ADR).
- Embeddings: local open-source model by default (no key needed); hosted embeddings optional.
- Verify current model names and package versions at project start; they change often.

### Zero-cost development ladder
Most development must cost $0. Real paid calls are reserved for final eval runs.

| Tier | Used for | Cost |
|---|---|---|
| 0. Fake LLM (`LLM_MODE=fake`) | Unit tests, API, UI, graph wiring, CI | $0 |
| 1. Record / replay (`LLM_MODE=replay`) | Re-running recorded real responses as fixtures | $0 after recording |
| 2. Local open-weight via Ollama (e.g. OpenAI's gpt-oss) | Real-model behaviour for text-only steps | $0 (runs on laptop) |
| 3. Paid API (OpenAI / Anthropic), cheapest tier, hard spend cap | One-time fixture recording (incl. vision steps) | ~$1–2 one-time |
| 4. Same, stronger tier | Final eval runs + demo only | Small, capped |

Note: GitHub Models (formerly a free hosted option) was retired on 2026-07-30 and is not available.

Acceptance: the full test suite and UI run with no API key; switching tier is an `.env` change only.

---

## 8. Data

| Data | Source | Notes |
|---|---|---|
| Policy forms | Publicly published personal auto policy forms, or authored samples modeled on them | Verify reuse terms; cite sources in README |
| Damage photos | Public car-damage dataset (e.g. CarDD) | **Verify license** before use; fall back to authored/CC-licensed images |
| Fraud labels | Public vehicle-insurance fraud dataset (e.g. Kaggle) used to seed claim histories and labels | **Verify license**; map columns to our schema |
| Claimants, policies, claim forms | Synthetic (generated) | Clearly marked synthetic |
| Eval set | ≥ 30 labeled claims: approve, deny, escalate, fraud, edge cases | `evals/dataset.jsonl` with expected decision, citations, payout |

---

## 9. Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (subgraphs, Postgres checkpointer) |
| LLM layer | LangChain `init_chat_model` (provider-agnostic) |
| Tools | FastMCP server + `langchain-mcp-adapters` |
| Mock core system | Small FastAPI service (separate container) |
| Database | PostgreSQL + pgvector (claims, users, audit, checkpoints, vectors) |
| Migrations | Alembic |
| API | FastAPI, SSE |
| Auth | PyJWT, pwdlib |
| Frontend | React + TypeScript (Vite), TanStack Query, Tailwind + shadcn/ui |
| Tracing | Langfuse or LangSmith |
| Python tooling | uv, ruff, pytest |
| Packaging | Docker Compose; cloud deploy (Azure or AWS — decide in ADR) |

---

## 10. Deliverables
- Running demo URL + `docker compose up` local path
- `evals/report.md` with the provider comparison table
- `docs/adr/` (at least: deterministic routing, single review gate, pgvector, model selection per step, CrewAI deferral)
- README: problem, architecture diagram, workflow table, screenshots, setup, eval results, limitations, synthetic-data disclaimer
- 2-minute demo video (business problem first)
- 1-page case summary (problem, approach, results, risks, next steps)

---

## 11. Roadmap

| Week | Outcome |
|---|---|
| 0 | Repo, Docker Compose (Postgres+pgvector), config, tracing, fake-LLM mode, data license checks |
| 1 | Data + seed, policy ingestion, coverage subgraph with citations, eval harness v0 |
| 2 | Intake (vision), MCP server + mock core system, payout; parent graph runs end-to-end from CLI |
| 3 | Fraud (parallel), review gate with checkpointing, letter loop; eval on 2 providers |
| 4 | FastAPI, SSE, auth/roles, audit log, redaction, retries |
| 5 | React pages incl. impact dashboard |
| 6 | Cloud deploy, eval report, ADRs, README, video, case summary |

---

## 12. Open decisions and risks

| Item | Status |
|---|---|
| Which 2 providers for the eval matrix | Open — depends on available credits |
| Cloud target (Azure vs AWS) | Open — ADR in week 6 |
| Dataset licenses (CarDD, Kaggle fraud set) | To verify in week 0 |
| Manual-handling baseline for impact dashboard | Use a stated assumption, labeled as such |
| Framework API churn (LangGraph, FastMCP, provider packages) | Pin versions once week 0 runs |
| Scope creep | Anything not in "Must" goes to a backlog, not the sprint |
