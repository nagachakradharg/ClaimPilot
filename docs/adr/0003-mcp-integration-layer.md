# ADR-0003: MCP server as the integration layer (fastmcp 4, no LangChain adapter)

- Status: Accepted
- Date: 2026-11-24

## Context
The agents need policy data, claim history, repair estimates and the insurer's legacy core claims system (FR-4.1). The requirements named FastMCP plus `langchain-mcp-adapters`. At project start, `langchain-mcp-adapters` (0.3.2) requires `mcp<2`, but fastmcp 4 requires `mcp` 2.x, so the two can't be installed together.

## Decision
- One MCP server (`claimpilot.mcp_server`, fastmcp 4) exposes six tools: `lookup_policy`, `get_claim_history`, `get_repair_estimates`, `get_vehicle_value`, `create_claim_record`, `update_claim_status`.
- The MCP server is the **anti-corruption layer**. Legacy codes (`STATUS_CD`, `LOSS_TYPE_CD`) and string amounts are mapped to typed results there, so graph code never sees legacy formats.
- Graphs call tools through `claimpilot.tools.ToolClient`, which uses fastmcp's own `Client`. It adds a timeout and a `ToolRecord` per call (audit, run_id).
- Tool calls in payout are **deterministic**: code chooses which tools to call. FR-4.3 allows an LLM to choose tools, but nothing in v1 needs it. Deterministic calls are cheaper, testable and auditable.
- The transport is in-process for dev, tests and the CLI. In Docker Compose and the cloud it is HTTP (`MCP_SERVER_URL`, `CORE_SYSTEM_URL`).
- Core-system calls retry 5xx and transport errors with backoff. Claim creation is idempotent on `external_ref`, so retries are safe.

## Consequences
- No LangChain adapter dependency. If an LLM tool-calling agent is added later, wrap `ToolClient` tools as LangChain `StructuredTool`s (about 20 lines), or switch to the adapter once it supports `mcp` 2.x.
- `claim_history.fraud_label` stays in the DB for evals only. No tool exposes it.
