import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { Badge, Card, DecisionBadge, ErrorNote, Spinner, StatusBadge } from "../components/ui";
import { cn, humanize, money, when } from "../lib/format";

const FILTERS = ["pending_review", "processing", "closed", "invalid", "failed", ""];

export default function Queue() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "pending_review";
  const q = useQuery({
    queryKey: ["claims", status],
    queryFn: () => api.claims(status || undefined),
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Claims queue</h1>
        <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1">
          {FILTERS.map((f) => (
            <button
              key={f || "all"}
              onClick={() => setParams(f === "pending_review" ? {} : { status: f })}
              className={cn(
                "rounded-md px-3 py-1 text-sm",
                status === f
                  ? "bg-blue-600 font-medium text-white shadow-sm"
                  : "text-slate-600 hover:text-slate-900",
              )}
            >
              {f ? humanize(f) : "all"}
            </button>
          ))}
        </div>
      </div>
      <ErrorNote error={q.error} />
      {q.isLoading && <Spinner />}
      <Card className="overflow-x-auto p-0">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2.5 font-medium">Claim</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Why it needs a human</th>
              <th className="px-4 py-2.5 text-right font-medium">Payout</th>
              <th className="px-4 py-2.5 font-medium">Filed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {q.data?.map((c) => (
              <tr key={c.claim_id} className="hover:bg-slate-50">
                <td className="px-4 py-2.5">
                  <Link to={`/claims/${c.claim_id}`} className="font-mono text-blue-600 hover:underline">
                    {c.claim_id}
                  </Link>
                  <div className="text-xs text-slate-500">
                    {c.policy_number} · {c.core_claim_id ?? "not registered"}
                  </div>
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1">
                    <StatusBadge status={c.status} />
                    <DecisionBadge decision={c.decision} />
                  </div>
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1">
                    {(c.review_reasons ?? []).map((r) => (
                      <Badge key={r} tone="amber">
                        {humanize(r)}
                      </Badge>
                    ))}
                    {c.error && <Badge tone="red">error</Badge>}
                  </div>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">{money(c.final_payout)}</td>
                <td className="px-4 py-2.5 text-slate-500">{when(c.created_at)}</td>
              </tr>
            ))}
            {q.data?.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                  Nothing here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
