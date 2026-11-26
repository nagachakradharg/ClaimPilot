import type { AuditEvent } from "../lib/api";
import { buildSpans, t, timeAxis } from "../lib/trace";
import { Badge } from "./ui";
import { cn, when } from "../lib/format";

// Trace timeline: per-node duration from node_start/node_end pairs, with the
// model and tool calls that ran inside each node, plus tokens and cost.

// Duration of a span excluding any review wait inside it (the review gate itself pauses).
const activeDuration = (start: number, end: number, waits: { start: number; end: number }[]) =>
  end - start - waits.reduce((n, w) => n + Math.max(0, Math.min(end, w.end) - Math.max(start, w.start)), 0);

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);

export function Timeline({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-slate-500">No events yet.</p>;
  const spans = buildSpans(events);
  const axis = timeAxis(events);
  const { t0, tEnd } = axis;

  const llm = events.filter((e) => e.kind === "llm");
  const tools = events.filter((e) => e.kind === "tool");
  const tokens = llm.reduce((n, e) => n + (e.input_tokens ?? 0) + (e.output_tokens ?? 0), 0);
  const cost = llm.reduce((n, e) => n + (e.cost_usd ?? 0), 0);
  const markers = events.filter((e) => e.kind === "status" || e.kind === "human");

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {[
          ["Active time", fmtMs(axis.activeMs)],
          ["Model calls", String(llm.length)],
          ["Tokens", tokens.toLocaleString()],
          ["Model cost", `$${cost.toFixed(4)}`],
          ["Tool calls", String(tools.length)],
          ...(axis.waits.length ? [["Review wait", fmtMs(axis.waitMs) + (axis.waits.some((w) => w.open) ? " (open)" : "")]] : []),
        ].map(([k, v]) => (
          <div key={k} className="rounded-md bg-slate-50 px-3 py-2">
            <div className="text-xs text-slate-500">{k}</div>
            <div className="font-semibold tabular-nums">{v}</div>
          </div>
        ))}
      </div>

      <div className="space-y-1">
        {spans.map((s, i) => {
          const end = s.end ?? tEnd;
          const left = axis.x(s.start);
          const width = Math.max(axis.x(end) - left, 0.4);
          return (
            <div key={i} className="grid grid-cols-[minmax(0,14rem)_1fr] items-start gap-3 text-xs sm:grid-cols-[16rem_1fr]">
              <div className="truncate pt-0.5 font-mono" style={{ paddingLeft: s.depth * 14 }} title={s.name}>
                {s.name.split("/").pop()}
                <span className="ml-1.5 text-slate-500">
                  {s.end == null ? "waiting" : fmtMs(activeDuration(s.start, end, axis.waits))}
                </span>
              </div>
              <div>
                <div className="relative h-4 rounded bg-slate-100">
                  {axis.waits.map((w, k) => (
                    <div
                      key={k}
                      title={`Waiting for an adjuster: ${fmtMs(w.end - w.start)} (not to scale)`}
                      className="absolute inset-y-0 bg-[repeating-linear-gradient(45deg,#e2e8f0_0,#e2e8f0_3px,#f8fafc_3px,#f8fafc_6px)]"
                      style={{ left: `${axis.x(w.start)}%`, width: `${axis.x(w.end) - axis.x(w.start)}%` }}
                    />
                  ))}
                  <div
                    className={cn(
                      "absolute inset-y-0 rounded",
                      s.end == null ? "bg-amber-400/70" : s.depth === 0 ? "bg-blue-500" : "bg-blue-300",
                    )}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  />
                </div>
                {s.calls.length > 0 && (
                  <div className="mt-1 mb-1.5 flex flex-wrap gap-1">
                    {s.calls.map((c, j) => (
                      <CallChip key={j} e={c} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Status and human events</h3>
        <ul className="space-y-1 text-sm">
          {markers.map((e, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <span className="tabular-nums text-slate-500">+{fmtMs(t(e.ts) - t0)}</span>
              <Badge tone={e.kind === "human" ? "violet" : "slate"}>{e.name}</Badge>
              <span className="text-slate-500">{e.actor}</span>
              {e.kind === "human" && typeof e.data.reason === "string" && <span>“{e.data.reason}”</span>}
              <span className="ml-auto hidden text-xs text-slate-400 sm:inline">{when(e.ts)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function CallChip({ e }: { e: AuditEvent }) {
  const isLlm = e.kind === "llm";
  const tok = isLlm && (e.input_tokens != null || e.output_tokens != null)
    ? ` · ${e.input_tokens ?? 0}→${e.output_tokens ?? 0} tok`
    : "";
  const attempts = Number(e.data.attempts ?? 1);
  return (
    <span
      title={JSON.stringify(e.data)}
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[11px]",
        e.error
          ? "bg-rose-100 text-rose-800"
          : isLlm
            ? "bg-violet-100 text-violet-800"
            : "bg-sky-100 text-sky-800",
      )}
    >
      {isLlm ? "LLM" : "tool"} {e.name} · {e.latency_ms != null ? fmtMs(e.latency_ms) : "?"}
      {tok}
      {isLlm && e.cost_usd ? ` · $${e.cost_usd.toFixed(4)}` : ""}
      {attempts > 1 ? ` · ${attempts} tries` : ""}
      {e.error ? " · error" : ""}
    </span>
  );
}
