import type { AuditEvent } from "./api";

// Trace timeline model.
export interface Span {
  name: string;
  depth: number;
  start: number;
  end: number | null; // null = still open (e.g. paused at the review gate)
  calls: AuditEvent[];
}

export const t = (iso: string) => new Date(iso).getTime();

const SLACK_MS = 500;
// Words that appear in many tool/node names and say nothing about where a call belongs.
const GENERIC_WORDS = new Set(["claim", "get", "update", "create", "status", "record", "set"]);

export function buildSpans(events: AuditEvent[]): Span[] {
  const spans: Span[] = [];
  const open = new Map<string, Span[]>();
  for (const e of events) {
    if (e.kind === "node_start") {
      const s: Span = { name: e.name, depth: e.name.split("/").length - 1, start: t(e.ts), end: null, calls: [] };
      spans.push(s);
      open.set(e.name, [...(open.get(e.name) ?? []), s]);
    } else if (e.kind === "node_end") {
      const q = open.get(e.name);
      const s = q?.shift();
      if (s) s.end = t(e.ts);
    }
  }
  // Attach each model/tool call to a span running when it started. Parallel branches
  // overlap in time, so prefer the span whose name shares the most words with the call
  // ("fraud_doc_consistency" -> "fraud/doc_consistency"), then the deepest.
  const words = (s: string) =>
    new Set(s.toLowerCase().split(/[/_]/).filter((w) => w && !GENERIC_WORDS.has(w)));
  const overlap = (a: Set<string>, b: Set<string>) => [...a].filter((w) => b.has(w)).length;
  for (const e of events) {
    if (e.kind !== "llm" && e.kind !== "tool") continue;
    const ts = t(e.ts) - (e.latency_ms ?? 0); // when the call started
    const w = words(e.name);
    const host = spans
      // timestamps come from two clocks (graph events vs. call records), so allow slack;
      // the name match below then picks the right step among the candidates
      .filter((s) => s.start <= ts + SLACK_MS && (s.end == null || ts <= s.end + SLACK_MS))
      .map((s) => ({
        s,
        score: overlap(w, words(s.name)),
        exact: Number(s.start <= ts + 1 && (s.end == null || ts <= s.end)), // no slack needed
      }))
      .sort(
        (a, b) =>
          b.score - a.score || b.exact - a.exact || b.s.depth - a.s.depth || b.s.start - a.s.start,
      )[0]?.s;
    host?.calls.push(e);
  }
  // Order rows as a tree: each top-level node followed by its own sub-steps, even when
  // two subgraphs (coverage || fraud) ran at the same time.
  const roots = spans.filter((s) => s.depth === 0);
  const rootOf = (s: Span) =>
    s.depth === 0
      ? s
      : roots.filter((r) => r.name === s.name.split("/")[0] && r.start <= s.start + 1).pop() ?? s;
  const order = new Map(roots.map((r, i) => [r, i]));
  return [...spans].sort(
    (a, b) =>
      (order.get(rootOf(a)) ?? 0) - (order.get(rootOf(b)) ?? 0) || a.depth - b.depth || a.start - b.start,
  );
}

// A claim can sit at the review gate for minutes or days. Drawn to scale, that wait would
// squash every active step into a sliver, so each wait is drawn as a short fixed band.

export interface Wait {
  start: number;
  end: number;
  open: boolean; // still waiting for an adjuster
}

export interface TimeAxis {
  t0: number;
  tEnd: number;
  waits: Wait[];
  activeMs: number; // wall clock minus review waits
  waitMs: number;
  x: (ms: number) => number; // epoch ms -> 0..100 (% of the drawn axis)
}

export function reviewWaits(events: AuditEvent[]): Wait[] {
  const waits: Wait[] = [];
  const tEnd = Math.max(...events.map((e) => t(e.ts)));
  events.forEach((e, i) => {
    if (e.kind !== "status" || e.name !== "pending_review") return;
    const resume = events.slice(i + 1).find((x) => x.kind === "human");
    waits.push({ start: t(e.ts), end: resume ? t(resume.ts) : tEnd, open: !resume });
  });
  return waits.filter((w) => w.end > w.start);
}

export function timeAxis(events: AuditEvent[]): TimeAxis {
  const t0 = t(events[0].ts);
  const tEnd = Math.max(...events.map((e) => t(e.ts)));
  const waits = reviewWaits(events);
  const waitMs = waits.reduce((n, w) => n + (w.end - w.start), 0);
  const activeMs = Math.max(tEnd - t0 - waitMs, 1);
  const band = activeMs * 0.04; // each wait is drawn as 4% of the active time
  const drawnTotal = activeMs + band * waits.length;
  const compress = (ms: number) => {
    let out = ms - t0;
    for (const w of waits) {
      const inside = Math.min(Math.max(ms - w.start, 0), w.end - w.start);
      out -= inside - (inside / (w.end - w.start)) * band;
    }
    return out;
  };
  return { t0, tEnd, waits, activeMs, waitMs, x: (ms) => (compress(ms) / drawnTotal) * 100 };
}
