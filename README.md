# ClaimPilot

Multi-agent processing for personal **auto** insurance claims: intake with vision, coverage RAG with clause citations, parallel fraud checks, deterministic payout, a human review gate, and decision letters. Built with LangGraph, FastAPI and React.

> **Synthetic demo.** All data is synthetic. The system only makes recommendations; an adjuster makes final decisions.

- Scope and acceptance criteria: [docs/requirements.md](docs/requirements.md)
- Decisions: [docs/adr/](docs/adr/)
- Data and licenses: [docs/data-sources.md](docs/data-sources.md)

## Status
**Week 4.** Done: weeks 0–3 plus the API: JWT login for 3 seeded roles, role-scoped claim endpoints, adjuster review actions, live progress over SSE (with history replay), a PII-redacted audit trail of every step, model call, tool call and human decision, and an impact-metrics endpoint. Next: the React UI (week 5).

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

Run the tests. They need no API key or database:

```bash
cd backend && uv run pytest
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
  src/claimpilot/   config, llm layer, db, api
  migrations/       Alembic
  tests/
docs/               requirements, ADRs, data sources
data/               policies, photos (synthetic / licensed only)
evals/              labeled eval set + report (week 1+)
infra/              container init scripts
```
