# ClaimPilot

Multi-agent processing for personal **auto** insurance claims: intake with vision, coverage RAG with clause citations, parallel fraud checks, deterministic payout, a human review gate, and decision letters. Built with LangGraph, FastAPI and React.

> **Synthetic demo.** All data is synthetic. The system only makes recommendations; an adjuster makes final decisions.

- Scope and acceptance criteria: [docs/requirements.md](docs/requirements.md)
- Decisions: [docs/adr/](docs/adr/)
- Data and licenses: [docs/data-sources.md](docs/data-sources.md)

## Status
**Week 5.** Done: weeks 0–4 plus the React UI (FR-9):
- **Submit claim** (claimant): form + photo upload, then live step-by-step progress over SSE and the decision letter once closed.
- **Adjuster queue**: filter by status, review-reason badges.
- **Claim detail**: extraction with per-field confidence, coverage with quoted policy clauses, fraud checks, deterministic payout steps and shop quotes, the letter, and approve / deny / override (reason required).
- **Trace timeline**: per-node duration bars (parallel coverage || fraud visible), the model and tool calls inside each node, tokens and cost.
- **Impact dashboard**: straight-through rate, escalations by reason, p50/p95 processing time, cost and tokens per claim, estimated hours saved (manual-handling baseline stated on the page).
- Persistent notice on every page: "Synthetic demo. Recommendations only; an adjuster makes final decisions."

Next: cloud deploy, docs and demo video (week 6).

### Demo users (synthetic)
| Role | Email | Can |
|---|---|---|
| Claimant | `casey.fofana@example.com` (policy CP-100003) | submit claims, see own status and letter |
| Adjuster | `adjuster@claimpilot.example` | review queue, approve / deny / override, audit, metrics |
| Auditor | `auditor@claimpilot.example` | read-only: all claims, audit, metrics |

Password for all: `claimpilot-demo` (set `DEMO_PASSWORD` for any shared deployment).

### API
`/auth/login`, `/auth/me`, `POST /claims` (multipart: `form` JSON + `photos`), `GET /claims`, `GET /claims/{id}`, `POST /claims/{id}/review`, `GET /claims/{id}/events` (SSE), `GET /claims/{id}/audit`, `GET /metrics`. Interactive docs: http://localhost:8000/docs

The CLI still works for quick runs: `uv run python -m claimpilot.cli submit|review|show`.

## Quick start (local)
Prerequisites: [uv](https://docs.astral.sh/uv/) and a Docker runtime.

```bash
cp .env.example .env
docker compose up -d db
cd backend && uv sync && uv run alembic upgrade head && uv run python -m claimpilot.seed
uv run uvicorn claimpilot.api.main:app --reload   # http://localhost:8000/docs
```

UI (Node 20+), in a second terminal. Vite proxies `/api` to the backend on port 8000:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Run the tests. They need no API key or database:

```bash
cd backend && uv run pytest
cd frontend && npm test        # trace/progress logic (vitest)
```

## LLM tiers (zero-cost development)
Set `LLM_MODE` in `.env`:

| Mode | What it does | Cost |
|---|---|---|
| `fake` | Canned per-node responses (tests, CI, UI) | $0 |
| `replay` | Serves responses recorded earlier | $0 |
| `record` | Calls the real model and saves responses | per call |
| `live` | Calls the real model | per call |

Run the coverage eval (writes `evals/report.md`):

```bash
cd backend && LLM_MODE=record uv run python -m claimpilot.evals.coverage
```

Models are set per node as `provider:model` (`LLM_DEFAULT_MODEL`, `LLM_NODE_MODELS`). Local default: `ollama:gemma4:12b` (text + vision; chosen by the coverage eval, see `evals/report.md`).

## Layout
```
backend/            FastAPI + LangGraph (uv project)
frontend/           React + TypeScript (Vite, TanStack Query, Tailwind)
  src/claimpilot/   config, llm layer, db, api
  migrations/       Alembic
  tests/
docs/               requirements, ADRs, data sources
data/               policies, photos (synthetic / licensed only)
evals/              labeled eval set + report (week 1+)
infra/              container init scripts
```
