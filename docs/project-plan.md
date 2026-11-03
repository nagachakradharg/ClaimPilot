# ClaimPilot: Multi-Agent Insurance Claims Processing Platform

An end-to-end, enterprise-style GenAI project built from course skills (RAG, LangChain, LangGraph, function calling, MCP, CrewAI/AG2, multimodal) with a React frontend.

---

## 1. Idea

ClaimPilot automates the lifecycle of an insurance claim: intake, coverage check, fraud investigation, payout estimation, human approval, and customer communication. Each stage is its own LangGraph workflow, coordinated by a supervisor graph.

**Why it stands out**
- Multimodal claims processing with fraud checks and adjuster approval is rare in portfolios.
- It shows enterprise concerns: approvals, role-based access, audit trails, evaluation.
- It demonstrates multiple, different workflow patterns (sequential, parallel, human-in-the-loop, multi-agent crews, supervisor routing).

**Data note:** use only synthetic claims, customers, and policies. Say so clearly in the README.

---

## 2. Workflows (each a separate LangGraph graph)

| # | Workflow | Pattern | What it does |
|---|----------|---------|--------------|
| 1 | Intake and triage | Sequential | Parse claim form and damage photos (multimodal), extract structured JSON, validate fields, classify claim type and severity |
| 2 | Coverage verification | RAG | Retrieve relevant policy clauses with citations, check exclusions, deductibles, and limits |
| 3 | Fraud investigation | Parallel | Agents check document consistency, claim history, and photo-vs-description mismatch at the same time; a synthesis node outputs a risk score and reasons |
| 4 | Damage assessment and payout | Function calling / MCP | Tools (`get_repair_estimates`, `lookup_policy`, `get_claim_history`) feed a recommended payout calculation |
| 5 | Human-in-the-loop approval | Interrupt / resume | Claims above a value threshold or with high fraud risk pause until an adjuster approves, rejects, or requests more info |
| 6 | Customer communication | Multi-agent crew | CrewAI or AG2 team drafts the decision letter; a reviewer agent checks tone and policy accuracy |
| 7 | Supervisor orchestrator | Multi-graph routing | Top-level graph routes each claim through the right sub-workflows and handles retries and failures |

### Supervisor flow (simplified)

```
Claim submitted
   -> Intake & Triage
   -> Coverage Verification
        -> (not covered) -> Communication (denial letter)
   -> Fraud Investigation (parallel)
        -> (high risk) -> Human Approval
   -> Payout Estimation
        -> (above threshold) -> Human Approval
   -> Communication (decision letter)
   -> Audit log closed
```

---

## 3. Architecture

- **Frontend:** React (Vite + TypeScript)
- **API layer:** FastAPI exposing workflows as endpoints (REST + server-sent events for live progress)
- **Orchestration:** LangGraph (one graph per workflow plus a supervisor)
- **Tools:** MCP server (FastMCP) exposing claim, policy, and estimate tools
- **Retrieval:** Vector store (Chroma for local; Qdrant or pgvector as an upgrade) over policy documents
- **LLM:** any chat model through LangChain (the course used watsonx Granite; swap freely via config)
- **Database:** PostgreSQL (claims, users, audit log, approvals); SQLite is fine for early development
- **Deployment:** Docker Compose; frontend on Vercel, backend on Render or similar

---

## 4. Repository Structure

```
claimpilot/
├── README.md
├── docker-compose.yml
├── .env.example
├── data/
│   ├── policies/              # synthetic policy PDFs/markdown
│   ├── claims/                # synthetic claim forms + damage photos
│   └── seed.py                # loads data into DB and vector store
├── backend/
│   ├── main.py                # FastAPI app
│   ├── config.py              # settings, model selection
│   ├── auth/                  # JWT login, role-based access control
│   ├── api/
│   │   ├── claims.py
│   │   ├── approvals.py
│   │   └── audit.py
│   ├── graphs/
│   │   ├── state.py           # shared TypedDict / Pydantic state
│   │   ├── intake.py
│   │   ├── coverage.py
│   │   ├── fraud.py
│   │   ├── payout.py
│   │   ├── approval.py
│   │   ├── communication.py   # CrewAI / AG2 crew
│   │   └── supervisor.py
│   ├── mcp_server/
│   │   └── server.py          # tools: lookup_policy, get_repair_estimates, ...
│   ├── rag/
│   │   ├── ingest.py          # chunking + embedding
│   │   └── retriever.py       # retrieval with metadata filters
│   ├── guardrails/
│   │   ├── pii.py             # redact sensitive data in logs
│   │   └── injection.py       # basic prompt-injection checks on uploads
│   ├── db/
│   │   ├── models.py
│   │   └── audit.py           # writes every step and tool call
│   └── tests/
├── evals/
│   ├── dataset.jsonl          # labeled claims with expected decisions
│   ├── run_evals.py           # pass rate, retrieval accuracy, latency, cost
│   └── report.md
└── frontend/
    ├── package.json
    └── src/
        ├── pages/
        │   ├── SubmitClaim.tsx        # form + photo upload
        │   ├── AdjusterDashboard.tsx  # queue, approve/reject
        │   ├── ClaimDetail.tsx        # status, citations, fraud report
        │   ├── WorkflowTrace.tsx      # step-by-step graph trace viewer
        │   └── AuditLog.tsx           # auditor view
        ├── components/
        └── api/
```

---

## 5. Requirements

### Roles (role-based access)
- **Claimant:** submit claims, view own claim status and decision letter
- **Adjuster:** see assigned queue, review fraud reports, approve/reject/request info
- **Auditor:** read-only access to all claims, traces, and audit logs

### Functional
- Upload claim form data and damage photos
- Structured extraction and validation of claim fields
- Coverage decision with cited policy clauses
- Parallel fraud analysis with a risk score and explanations
- Payout recommendation with the calculation shown
- Workflow pauses for human approval and resumes after a decision
- Generated decision letter, reviewed by a second agent
- Live progress updates in the UI

### Non-functional
- Every agent step, tool call, and decision is written to the audit log
- Retries, timeouts, and fallbacks on LLM and tool failures
- Sensitive fields redacted in logs
- Token and latency tracking per claim
- Reproducible setup with Docker Compose

### Python packages (unpinned; pin versions once it runs)
```
fastapi
uvicorn
pydantic
sqlalchemy
psycopg2-binary
python-jose
passlib
python-multipart
langchain
langchain-core
langchain-community
langchain-ibm          # if using watsonx; or another provider package
langgraph
crewai                 # or ag2
fastmcp
chromadb
pytest
httpx
```

### Frontend packages
```
react, react-dom, react-router-dom
typescript, vite
@tanstack/react-query
tailwindcss
reactflow              # optional, for the workflow trace visual
```

### Optional upgrades
- **Tracing:** LangSmith or Langfuse
- **Eval library:** RAGAS, or keep a simple custom script
- **Vector store:** Qdrant or pgvector instead of Chroma
- **Streaming:** server-sent events or WebSockets for live status

---

## 6. Build Roadmap

1. **Week 1: Foundations.** Synthetic data, policy ingestion, vector store, a basic coverage-check graph with citations.
2. **Week 2: Core workflows.** Intake (multimodal), payout with MCP tools, fraud graph with parallel nodes.
3. **Week 3: Control flow.** Human-in-the-loop interrupts, communication crew, supervisor graph.
4. **Week 4: Enterprise layer.** FastAPI, auth and roles, audit log, guardrails, retries.
5. **Week 5: Frontend.** Submit page, adjuster dashboard, trace viewer, audit view.
6. **Week 6: Quality and polish.** Eval dataset and report, Docker, deployment, README, demo GIF.

---

## 7. Evaluation Plan

- Create 30-50 synthetic labeled claims (approve, deny, escalate, suspected fraud).
- Measure: decision accuracy, escalation precision/recall, citation correctness, latency per claim, cost per claim.
- Run evals before each major change and record results in `evals/report.md`.
- Include a few adversarial cases (prompt injection inside an uploaded document, contradictory photos).

---

## 8. Notes and Cautions

- **Synthetic data only.** Never use real claims or personal data.
- **Human decides.** The system recommends; an adjuster approves high-impact outcomes. State this in the README and UI.
- **Fairness and explainability.** Always show the reasons and cited clauses behind a fraud score or denial. Do not auto-deny based on a fraud score alone.
- **Start small.** Get one workflow working end to end before adding the supervisor.
- **Keep model access configurable** so the project runs with whichever provider you have.
- **Version pins:** check current package versions when you start, since LangGraph, CrewAI, and FastMCP APIs change often.

---

## 9. README Checklist

- One-paragraph problem statement and a demo GIF
- Architecture diagram and supervisor flow diagram
- Workflow table (like section 2)
- Screenshots of the adjuster dashboard and trace viewer
- Setup in under five commands (`docker compose up`)
- Eval results table
- Limitations and next steps
- Synthetic-data disclaimer

---

## 10. Interview Talking Points

- Why separate graphs plus a supervisor instead of one large graph
- How parallel fraud analysis reduces latency and how conflicting results are resolved
- How human-in-the-loop interrupts persist state and resume
- How the audit log supports compliance reviews
- What the evals showed and what you changed because of them
- Tradeoffs: cost vs. accuracy, model choice per step, failure handling
