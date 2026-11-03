# ADR-0001: Deterministic routing in the parent graph

- Status: Accepted
- Date: 2026-11-24

## Context
The parent graph sends each claim through intake, coverage, fraud, payout, review and letter. One option is an LLM "supervisor" that picks the next step. Claims decisions must be auditable and reproducible, and a regulator or client will ask *why* a claim went where it did.

## Decision
Routing uses plain conditional edges in code, driven by typed state (coverage result, fraud score, payout and thresholds from config). LLMs work **inside** nodes (extraction, coverage reasoning, letter writing). They never choose the path.

## Consequences
- Every route can be explained by a rule and tested without a model call.
- Adding a new path means writing code, not a prompt. That is intentional.
- We give up "agentic" flexibility, which this domain doesn't need.
