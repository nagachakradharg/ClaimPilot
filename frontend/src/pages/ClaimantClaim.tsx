import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import { useClaimEvents } from "../lib/useClaimEvents";
import { ProgressSteps } from "../components/Progress";
import { Card, CardTitle, DecisionBadge, ErrorNote, Spinner, StatusBadge, money } from "../components/ui";

export default function ClaimantClaim() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["claim", id], queryFn: () => api.claim(id!),
    refetchInterval: (q) =>
      ["submitted", "processing", "pending_review"].includes(q.state.data?.status ?? "") ? 5000 : false,
  });
  const { events } = useClaimEvents(id, (e) => {
    if (e.kind === "status") qc.invalidateQueries({ queryKey: ["claim", id] });
  }, q.data?.status);
  const c = q.data;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-mono text-xl font-semibold">{id}</h1>
        {c && <StatusBadge status={c.status} />}
        {c && <DecisionBadge decision={c.decision} />}
      </div>
      <ErrorNote error={q.error} />
      {q.isLoading && <Spinner />}

      {c?.status === "invalid" && (
        <Card className="border-rose-200">
          <CardTitle>We couldn't accept this claim</CardTitle>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {(c.errors ?? []).map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </Card>
      )}

      {c && c.status !== "invalid" && (
        <Card>
          <CardTitle>Progress</CardTitle>
          <ProgressSteps events={events} finalStatus={c.status} />
          {c.status === "failed" && (
            <p className="mt-3 text-sm text-rose-600">
              Something went wrong while processing. An adjuster has been notified.
            </p>
          )}
        </Card>
      )}

      {c?.letter && (
        <Card>
          <CardTitle right={c.final_payout != null && <span className="font-semibold">{money(c.final_payout)}</span>}>
            Decision letter
          </CardTitle>
          <h3 className="mb-3 font-medium">{c.letter.subject}</h3>
          <div className="whitespace-pre-wrap text-sm leading-relaxed">{c.letter.body}</div>
        </Card>
      )}
    </div>
  );
}
