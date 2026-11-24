import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, CardTitle, ErrorNote, Spinner } from "../components/ui";
import { humanize, pct } from "../lib/format";

const secs = (ms: number | null) => (ms == null ? "—" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </Card>
  );
}

export default function Dashboard() {
  const q = useQuery({ queryKey: ["metrics"], queryFn: api.metrics, refetchInterval: 10000 });
  const m = q.data;
  const reasons = Object.entries(m?.escalation_by_reason ?? {});
  const maxReason = Math.max(1, ...reasons.map(([, n]) => n));
  const statuses = Object.entries(m?.by_status ?? {});

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Impact</h1>
      <ErrorNote error={q.error} />
      {q.isLoading && <Spinner />}
      {m && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Claims processed" value={String(m.claims_total)} sub={`${m.decided} decided`} />
            <Stat label="Straight-through rate" value={pct(m.straight_through_rate)} sub="decided claims auto-approved" />
            <Stat label="Escalation rate" value={pct(m.escalation_rate)} sub="claims sent to an adjuster" />
            <Stat
              label="Est. hours saved"
              value={m.est_handling_hours_saved.toFixed(1)}
              sub={`at ${m.manual_handling_minutes_assumption} min per claim (assumed)`}
            />
            <Stat label="Processing time p50" value={secs(m.processing_ms_p50)} sub="submit → decision, excl. review wait" />
            <Stat label="Processing time p95" value={secs(m.processing_ms_p95)} />
            <Stat
              label="Model cost per claim"
              value={m.llm_cost_per_claim_usd == null ? "—" : `$${m.llm_cost_per_claim_usd.toFixed(4)}`}
              sub="$0 on the local model"
            />
            <Stat
              label="Tokens per claim"
              value={m.tokens_per_claim == null ? "—" : Math.round(m.tokens_per_claim).toLocaleString()}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardTitle>Escalations by reason</CardTitle>
              {reasons.length === 0 ? (
                <p className="text-sm text-slate-500">No escalations yet.</p>
              ) : (
                <ul className="space-y-2.5" aria-label="Escalations by reason">
                  {reasons.map(([reason, n]) => (
                    <li key={reason} className="group text-sm" title={`${humanize(reason)}: ${n}`}>
                      <div className="mb-1 flex justify-between gap-3">
                        <span className="truncate">{humanize(reason)}</span>
                        <span className="tabular-nums text-slate-600">{n}</span>
                      </div>
                      <div className="h-2 rounded-sm bg-slate-100">
                        <div
                          className="h-2 rounded-r bg-blue-500 group-hover:bg-blue-600"
                          style={{ width: `${(n / maxReason) * 100}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-xs text-slate-500">A claim can trigger more than one reason.</p>
            </Card>
            <Card>
              <CardTitle>Claims by status</CardTitle>
              <table className="w-full text-sm">
                <tbody className="divide-y divide-slate-100">
                  {statuses.map(([s, n]) => (
                    <tr key={s}>
                      <td className="py-1.5">{humanize(s)}</td>
                      <td className="py-1.5 text-right tabular-nums">{n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>

          <p className="text-xs text-slate-500">
            Hours saved is an estimate: auto-approved claims × an assumed{" "}
            {m.manual_handling_minutes_assumption} minutes of manual handling each (set by
            MANUAL_HANDLING_MINUTES). It is a stated assumption, not a measurement.
          </p>
        </>
      )}
    </div>
  );
}
