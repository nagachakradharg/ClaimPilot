# ADR-0002: One LLM access layer with fake / replay / record / live modes

- Status: Accepted
- Date: 2026-11-24

## Context
The requirements call for provider-agnostic models chosen per node (NFR-4), a CI that runs with no API keys (NFR-9), retries, timeouts and schema repair (NFR-3), per-call cost and audit data (NFR-1), and development that costs close to $0 (§7).

## Decision
Every model call goes through `claimpilot.llm.LLMClient`, addressed by **node name**:

- `structured(node, Schema, messages)` returns a validated pydantic object; `text(node, messages)` returns a string.
- The model is set by `LLM_DEFAULT_MODEL` / `LLM_NODE_MODELS` as `provider:model` and built with LangChain `init_chat_model`.
- `LLM_MODE` picks the tier:
  - `fake`: canned per-node responses. They still have to validate against the schema.
  - `replay`: recorded responses, keyed by a hash of node, model, schema and messages.
  - `record`: real call, with the response saved for replay.
  - `live`: real call, nothing saved.
- Retries with backoff and a per-call timeout are applied uniformly. Provider-level retries are disabled.
- Structured output gets one repair round before raising `LLMCallError`.
- Each call emits a `CallRecord` (node, model, tokens, latency, attempts, run_id) to listeners. The audit log and metrics attach there.

## Consequences
- Switching provider or tier is an `.env` change only.
- Changing a prompt invalidates its replay fixtures, so they have to be re-recorded. That is the price of determinism.
- Tracing (Langfuse) is optional and off without keys. The local `CallRecord` audit doesn't depend on it.
