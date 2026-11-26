import { describe, expect, it } from "vitest";
import type { AuditEvent } from "./api";
import { buildSpans, t, timeAxis } from "./trace";
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

it("attaches a call whose start is slightly before its step (clock skew)", () => {
  const events = [
    ev(0, "node_start", "coverage"),
    ev(0, "node_start", "coverage/retrieve"),
    ev(100, "node_end", "coverage/retrieve"),
    ev(101, "node_start", "coverage/assess"),
    ev(5095, "llm", "coverage_assess", 5000), // computed start 95 ms: inside retrieve
    ev(5100, "node_end", "coverage/assess"),
    ev(5101, "node_end", "coverage"),
  ];
  const by = Object.fromEntries(buildSpans(events).map((s) => [s.name, s.calls.map((c) => c.name)]));
  expect(by["coverage/assess"]).toEqual(["coverage_assess"]);
  expect(by["coverage/retrieve"]).toEqual([]);
});

it("does not match calls on generic words like 'claim'", () => {
  const events = [
    ev(0, "node_start", "register_core"),
    ev(300, "tool", "create_claim_record", 200), // starts at 100, inside register_core
    ev(450, "node_end", "register_core"),
    ev(451, "node_start", "fraud"),
    ev(452, "node_start", "fraud/claim_history"),
    ev(900, "node_end", "fraud/claim_history"),
    ev(901, "node_end", "fraud"),
  ];
  const by = Object.fromEntries(buildSpans(events).map((s) => [s.name, s.calls.map((c) => c.name)]));
  expect(by["register_core"]).toEqual(["create_claim_record"]);
});

describe("timeAxis", () => {
  it("collapses the review wait so active steps keep their scale", () => {
    const events = [
      ev(0, "status", "submitted"),
      ev(0, "node_start", "intake"),
      ev(1000, "node_end", "intake"),
      ev(1000, "status", "pending_review"),
      ev(601000, "human", "review_approve"), // 10 minutes later
      ev(601000, "node_start", "letter"),
      ev(602000, "node_end", "letter"),
    ];
    const axis = timeAxis(events);
    expect(axis.waitMs).toBe(600000);
    expect(axis.activeMs).toBe(2000);
    // intake (first active second) takes roughly half the drawn axis, not 0.2%
    const intakeWidth = axis.x(t(events[2].ts)) - axis.x(t(events[1].ts));
    expect(intakeWidth).toBeGreaterThan(40);
    expect(axis.x(t(events[6].ts))).toBeCloseTo(100, 5);
  });

  it("marks a wait with no decision yet as open", () => {
    const axis = timeAxis([ev(0, "node_start", "intake"), ev(5, "status", "pending_review"), ev(9, "node_start", "human_review")]);
    expect(axis.waits).toHaveLength(1);
    expect(axis.waits[0].open).toBe(true);
  });
});
