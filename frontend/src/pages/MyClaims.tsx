import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Button, Card, DecisionBadge, ErrorNote, Spinner, StatusBadge } from "../components/ui";
import { money, when } from "../lib/format";

export default function MyClaims() {
  const q = useQuery({ queryKey: ["claims"], queryFn: () => api.claims(), refetchInterval: 5000 });
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">My claims</h1>
        <Link to="/submit">
          <Button>New claim</Button>
        </Link>
      </div>
      <ErrorNote error={q.error} />
      {q.isLoading && <Spinner />}
      {q.data?.length === 0 && (
        <Card className="text-sm text-slate-600">
          No claims yet. File one with photos of the damage.
        </Card>
      )}
      <div className="space-y-2">
        {q.data?.map((c) => (
          <Link key={c.claim_id} to={`/claims/${c.claim_id}`} className="block">
            <Card className="flex flex-wrap items-center gap-3 py-3 hover:border-blue-300">
              <span className="font-mono text-sm">{c.claim_id}</span>
              <StatusBadge status={c.status} />
              <DecisionBadge decision={c.decision} />
              <span className="ml-auto text-sm text-slate-500">{when(c.created_at)}</span>
              {c.final_payout != null && <span className="text-sm font-medium">{money(c.final_payout)}</span>}
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
