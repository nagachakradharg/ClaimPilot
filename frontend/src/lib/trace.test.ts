import { describe, expect, it } from "vitest";
import type { AuditEvent } from "./api";
import { buildSpans } from "./trace";
import { stepStates } from "./progress";

const ev = (ms: number, kind: AuditEvent["kind"], name: string, latency_ms: number | null = null): AuditEvent => ({
  ts: new Date(Date.UTC(2026, 0, 1, 0, 0, 0, ms)).toISOString(),
  kind,
  name,
  actor: "system",
  latency_ms,
  input_tokens: null,
  output_tokens: null,
  cost_usd: null,
  error: null,
  data: {},
});

// coverage || fraud run in parallel, so their sub-steps interleave in time.
const parallel = [
  ev(0, "node_start", "register_core"),
  ev(20, "tool", "update_claim_status", 10),
  ev(21, "node_end", "register_core"),
  ev(22, "node_start", "coverage"),
  ev(22, "node_start", "fraud"),
  ev(23, "node_start", "coverage/assess"),
  ev(24, "node_start", "fraud/doc_consistency"),
  ev(40, "llm", "fraud_doc_consistency", 15),
  ev(41, "node_end", "fraud/doc_consistency"),
  ev(50, "llm", "coverage_assess", 20),
  ev(51, "node_end", "coverage/assess"),
  ev(52, "node_end", "coverage"),
  ev(53, "node_end", "fraud"),
];

describe("buildSpans", () => {
  it("orders sub-steps under their own parent", () => {
    expect(buildSpans(parallel).map((s) => s.name)).toEqual([
      "register_core",
      "coverage",
      "coverage/assess",
      "fraud",
      "fraud/doc_consistency",
    ]);
  });

  it("attaches calls to the matching parallel node, by start time and name", () => {
    const by = Object.fromEntries(buildSpans(parallel).map((s) => [s.name, s.calls.map((c) => c.name)]));
    expect(by["register_core"]).toEqual(["update_claim_status"]);
    expect(by["coverage/assess"]).toEqual(["coverage_assess"]);
    expect(by["fraud/doc_consistency"]).toEqual(["fraud_doc_consistency"]);
  });

  it("leaves a paused node open", () => {
    const spans = buildSpans([ev(0, "node_start", "human_review")]);
    expect(spans[0].end).toBeNull();
  });
});

describe("stepStates", () => {
  it("shows the review gate waiting, then done after a human decision", () => {
    const paused = [ev(0, "node_start", "human_review"), ev(1, "status", "pending_review")];
    expect(stepStates(paused).review).toBe("waiting");
    expect(stepStates([...paused, ev(2, "human", "review_deny")]).review).toBe("done");
  });

  it("marks auto-approval as skipping review", () => {
    const s = stepStates([ev(0, "node_start", "auto_approve"), ev(1, "node_end", "auto_approve")]);
    expect(s.review).toBe("skipped");
  });
});
