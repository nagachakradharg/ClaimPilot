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
  const words = (s: string) => new Set(s.toLowerCase().split(/[/_]/).filter(Boolean));
  const overlap = (a: Set<string>, b: Set<string>) => [...a].filter((w) => b.has(w)).length;
  for (const e of events) {
    if (e.kind !== "llm" && e.kind !== "tool") continue;
    const ts = t(e.ts) - (e.latency_ms ?? 0); // when the call started
    const w = words(e.name);
    const host = spans
      .filter((s) => s.start <= ts + 1 && (s.end == null || ts <= s.end))
      .map((s) => ({ s, score: overlap(w, words(s.name)) }))
      .sort((a, b) => b.score - a.score || b.s.depth - a.s.depth || b.s.start - a.s.start)[0]?.s;
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
