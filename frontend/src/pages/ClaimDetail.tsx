import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, type ClaimRun, type ReviewDecision } from "../lib/api";
import { useAuth } from "../lib/authContext";
import { useClaimEvents } from "../lib/useClaimEvents";
import { Timeline } from "../components/Timeline";
import { ProgressSteps } from "../components/Progress";
import { Photos } from "../components/Photos";
import { Badge, Button, Card, CardTitle, DecisionBadge, ErrorNote, Field, Input, Spinner, StatusBadge } from "../components/ui";
import { cn, humanize, money, pct } from "../lib/format";

export default function ClaimDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"overview" | "trace">("overview");
  const claim = useQuery({ queryKey: ["claim", id], queryFn: () => api.claim(id!),
    refetchInterval: (q) =>
      ["submitted", "processing", "pending_review"].includes(q.state.data?.status ?? "") ? 5000 : false,
  });
  const audit = useQuery({ queryKey: ["audit", id], queryFn: () => api.audit(id!) });
  const { events } = useClaimEvents(id, (e) => {
    // live: refresh the record on status changes, the trace on every node/call event
    if (e.kind === "status" || e.kind === "human") qc.invalidateQueries({ queryKey: ["claim", id] });
    qc.invalidateQueries({ queryKey: ["audit", id] });
  }, claim.data?.status);

  const c = claim.data;
  const run = c?.detail ?? null;

  return (
    <div className="space-y-4">
      <Link to="/" className="text-sm text-slate-500 hover:text-slate-800">
        ← Queue
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-mono text-xl font-semibold">{id}</h1>
        {c && <StatusBadge status={c.status} />}
        {c && <DecisionBadge decision={c.decision} />}
        {c?.core_claim_id && <span className="text-sm text-slate-500">Core system: {c.core_claim_id}</span>}
        <div className="ml-auto flex gap-1 rounded-lg bg-slate-100 p-1">
          {(["overview", "trace"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "rounded-md px-3 py-1 text-sm capitalize",
                tab === t ? "bg-blue-600 font-medium text-white shadow-sm" : "text-slate-600",
              )}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <ErrorNote error={claim.error} />
      {claim.isLoading && <Spinner />}
      {c && (c.status === "processing" || c.status === "submitted") && (
        <Card className="max-w-md">
          <CardTitle>Processing (live)</CardTitle>
          <ProgressSteps events={events} finalStatus={c.status} />
        </Card>
      )}
      {c?.error && <ErrorNote error={c.error} />}

      {tab === "trace" ? (
        <Card>
          <CardTitle>Trace timeline</CardTitle>
          <ErrorNote error={audit.error} />
          {audit.data && <Timeline events={audit.data} />}
        </Card>
      ) : (
        run && (
          <Overview run={run} reasons={c?.review_reasons ?? []} status={c!.status} assignedTo={c?.assigned_to ?? null} />
        )
      )}
    </div>
  );
}

function Overview({
  run,
  reasons,
  status,
  assignedTo,
}: {
  run: ClaimRun;
  reasons: string[];
  status: string;
  assignedTo: string | null;
}) {
  const { user } = useAuth();
  const waiting = status === "pending_review" || status === "pending_letter";
  const canAct = user?.role === "adjuster" && (!assignedTo || assignedTo === user.email);
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        {reasons.length > 0 && (
          <Card className="border-amber-200 bg-amber-50/50">
            <CardTitle>Sent to review because</CardTitle>
            <div className="flex flex-wrap gap-1.5">
              {reasons.map((r) => (
                <Badge key={r} tone="amber">
                  {humanize(r)}
                </Badge>
              ))}
            </div>
          </Card>
        )}
        <Photos claimId={run.claim_id} />
        <Intake run={run} />
        <Coverage run={run} />
        <Fraud run={run} />
        <Payout run={run} />
        {run.letter && <LetterCard run={run} />}
      </div>
      <div className="space-y-4">
        {waiting && user?.role === "adjuster" && <Assignment claimId={run.claim_id} assignedTo={assignedTo} />}
        {status === "pending_review" && canAct && <ReviewActions run={run} />}
        {status === "pending_letter" && canAct && run.letter && <LetterEditor run={run} />}
        {status === "pending_review" && user?.role === "auditor" && (
          <Card className="text-sm text-slate-600">Auditors have read-only access.</Card>
        )}
        {run.review && (
          <Card>
            <CardTitle>Adjuster decision</CardTitle>
            <dl className="space-y-1 text-sm">
              <Row k="Action">{run.review.action}</Row>
              <Row k="By">{run.review.adjuster}</Row>
              {run.review.payout_amount != null && <Row k="Payout">{money(run.review.payout_amount)}</Row>}
              {run.review.reason && <Row k="Reason">{run.review.reason}</Row>}
            </dl>
          </Card>
        )}
        <Card>
          <CardTitle>Recommendation</CardTitle>
          <dl className="space-y-1 text-sm">
            <Row k="Coverage">{run.coverage ? humanize(run.coverage.decision) : "—"}</Row>
            <Row k="Fraud risk">{run.fraud ? pct(run.fraud.score) : "—"}</Row>
            <Row k="Payout">{money(run.payout?.result?.payout)}</Row>
            <Row k="Final payout">{money(run.final_payout)}</Row>
          </dl>
        </Card>
      </div>
    </div>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

function Intake({ run }: { run: ClaimRun }) {
  const x = run.intake?.extraction;
  const low = new Set(run.intake?.low_confidence_fields ?? []);
  if (!run.intake) return null;
  return (
    <Card>
      <CardTitle>Extraction (from the form and photos)</CardTitle>
      {run.intake.errors.length > 0 && (
        <ul className="mb-3 list-disc pl-5 text-sm text-rose-600">
          {run.intake.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {x && (
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {(
            [
              ["Claim type", x.claim_type, "claim_type"],
              ["Severity", x.severity, "severity"],
              ["Incident date", x.incident_date, "incident_date"],
              ["Location", x.location, "location"],
              ["Vehicle", x.vehicle ? [x.vehicle.color, x.vehicle.make, x.vehicle.model].filter(Boolean).join(" ") : null, "vehicle"],
              ["Damage", x.damage_areas.join(", "), "damage_areas"],
              ["Cause", x.described_cause, "cause"],
              ["Claimed amount", money(x.claimed_amount), ""],
            ] as const
          ).map(([label, value, key]) => (
            <div key={label}>
              <dt className="text-xs text-slate-500">
                {label}
                {key && x.confidence[key] != null && (
                  <span className={cn("ml-1.5", low.has(key) ? "text-rose-600" : "text-slate-400")}>
                    {pct(x.confidence[key])}
                  </span>
                )}
              </dt>
              <dd>{value || "—"}</dd>
            </div>
          ))}
        </dl>
      )}
      {run.intake.flags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {run.intake.flags.map((f) => (
            <Badge key={f} tone="amber">
              {humanize(f)}
            </Badge>
          ))}
        </div>
      )}
    </Card>
  );
}

function Coverage({ run }: { run: ClaimRun }) {
  const cov = run.coverage;
  if (!cov) return null;
  const tone = cov.decision === "covered" ? "green" : cov.decision === "not_covered" ? "red" : "amber";
  return (
    <Card>
      <CardTitle right={<Badge tone={tone}>{humanize(cov.decision)}</Badge>}>Coverage</CardTitle>
      <p className="mb-3 text-sm">{cov.reasoning}</p>
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-600">
        <span>Part: {cov.coverage_part ? humanize(cov.coverage_part) : "—"}</span>
        <span>Deductible: {money(cov.deductible)}</span>
        <span>Limit: {money(cov.limit)}</span>
      </div>
      <div className="space-y-2">
        {cov.citations.map((ct) => (
          <blockquote
            key={ct.clause_id + ct.quote}
            className="border-l-4 border-blue-300 bg-slate-50 py-2 pr-3 pl-3 text-sm"
          >
            <span className="mr-2 font-mono text-xs font-semibold text-blue-600">{ct.clause_id}</span>“
            {ct.quote}”
          </blockquote>
        ))}
      </div>
      {cov.flags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {cov.flags.map((f) => (
            <Badge key={f} tone="amber">
              {humanize(f)}
            </Badge>
          ))}
        </div>
      )}
    </Card>
  );
}

function Fraud({ run }: { run: ClaimRun }) {
  const f = run.fraud;
  if (!f) return null;
  return (
    <Card>
      <CardTitle right={<Badge tone={f.score >= 0.6 ? "red" : f.score >= 0.3 ? "amber" : "green"}>risk {pct(f.score)}</Badge>}>
        Fraud checks
      </CardTitle>
      <div className="space-y-3">
        {f.checks.map((ch) => (
          <div key={ch.name}>
            <div className="flex items-center gap-3 text-sm">
              <span className="w-36 shrink-0 font-medium">{humanize(ch.name)}</span>
              <div className="h-2 flex-1 rounded bg-slate-100">
                <div
                  className={cn("h-2 rounded", ch.risk >= 0.6 ? "bg-rose-500" : ch.risk >= 0.3 ? "bg-amber-400" : "bg-emerald-500")}
                  style={{ width: `${Math.max(ch.risk * 100, 2)}%` }}
                />
              </div>
              <span className="w-10 text-right tabular-nums text-slate-500">{pct(ch.risk)}</span>
            </div>
            {ch.findings.length > 0 && (
              <ul className="mt-1 ml-36 list-disc pl-5 text-xs text-slate-600">
                {ch.findings.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

function Payout({ run }: { run: ClaimRun }) {
  const p = run.payout;
  if (!p?.result) return null;
  return (
    <Card>
      <CardTitle right={<span className="font-semibold">{money(p.result.payout)}</span>}>
        Payout (deterministic){p.result.total_loss && " · total loss"}
      </CardTitle>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-slate-100">
          {p.result.steps.map((s) => (
            <tr key={s.label}>
              <td className="py-1.5">
                {s.label}
                {s.clause_id && <span className="ml-2 font-mono text-xs text-blue-600">{s.clause_id}</span>}
                {s.note && <div className="text-xs text-slate-500">{s.note}</div>}
              </td>
              <td className="py-1.5 text-right tabular-nums">{money(s.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {p.estimates && p.estimates.quotes.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-slate-500">
            {p.estimates.quotes.length} shop quote{p.estimates.quotes.length > 1 ? "s" : ""}
          </summary>
          <div className="mt-2 space-y-2">
            {p.estimates.quotes.map((q) => (
              <div key={q.shop} className="rounded bg-slate-50 p-2">
                <div className="flex justify-between font-medium">
                  <span>{q.shop}</span>
                  <span>{money(q.total)}</span>
                </div>
                {q.line_items.map((li) => (
                  <div key={li.damage_area + li.description} className="flex justify-between text-xs text-slate-500">
                    <span>
                      {li.damage_area}: {li.description}
                    </span>
                    <span>{money(li.total)}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </details>
      )}
      <div className="mt-2 flex flex-wrap gap-1">
        {p.flags.map((f) => (
          <Badge key={f} tone="amber">
            {humanize(f)}
          </Badge>
        ))}
      </div>
    </Card>
  );
}

function LetterCard({ run }: { run: ClaimRun }) {
  const l = run.letter!;
  return (
    <Card>
      <CardTitle
        right={
          <div className="flex gap-1">
            {l.revisions > 0 && <Badge>revised {l.revisions}×</Badge>}
            {l.edited_by && <Badge tone="violet">edited by {l.edited_by}</Badge>}
            {l.needs_adjuster_edit && <Badge tone="red">needs adjuster edit</Badge>}
          </div>
        }
      >
        Decision letter
      </CardTitle>
      <h3 className="mb-2 font-medium">{l.subject}</h3>
      <div className="whitespace-pre-wrap text-sm leading-relaxed">{l.body}</div>
      {l.issues.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-xs text-rose-600">
          {l.issues.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ReviewActions({ run }: { run: ClaimRun }) {
  const qc = useQueryClient();
  const [action, setAction] = useState<ReviewDecision["action"]>("approve");
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState(String(run.payout?.result?.payout ?? ""));
  const m = useMutation({
    mutationFn: () =>
      api.review(run.claim_id, {
        action,
        reason: reason || null,
        payout_amount: action === "override" ? Number(amount) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["claim", run.claim_id] });
      qc.invalidateQueries({ queryKey: ["claims"] });
    },
  });
  const needsReason = action !== "approve";
  return (
    <Card className="border-blue-200">
      <CardTitle>Your decision</CardTitle>
      <div className="mb-3 grid grid-cols-3 gap-1 rounded-lg bg-slate-100 p-1">
        {(["approve", "deny", "override"] as const).map((a) => (
          <button
            key={a}
            onClick={() => setAction(a)}
            className={cn(
              "rounded-md py-1 text-sm capitalize",
              action === a ? "bg-blue-600 font-medium text-white shadow-sm" : "text-slate-600",
            )}
          >
            {a}
          </button>
        ))}
      </div>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        {action === "approve" && (
          <p className="text-sm text-slate-600">
            Pays the recommended {money(run.payout?.result?.payout)} and sends the letter.
          </p>
        )}
        {action === "override" && (
          <Field label="Payout amount">
            <Input type="number" min={0} step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
          </Field>
        )}
        <Field label={needsReason ? "Reason (required)" : "Note (optional)"}>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            required={needsReason}
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
          />
        </Field>
        <ErrorNote error={m.error} />
        <Button
          type="submit"
          variant={action === "deny" ? "danger" : "primary"}
          className="w-full capitalize"
          disabled={m.isPending || m.isSuccess}
        >
          {m.isSuccess ? "Submitted" : m.isPending ? "Submitting…" : action}
        </Button>
      </form>
    </Card>
  );
}

function LetterEditor({ run }: { run: ClaimRun }) {
  const qc = useQueryClient();
  const l = run.letter!;
  const [subject, setSubject] = useState(l.subject);
  const [body, setBody] = useState(l.body);
  const m = useMutation({
    mutationFn: () => api.editLetter(run.claim_id, { subject, body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["claim", run.claim_id] });
      qc.invalidateQueries({ queryKey: ["claims"] });
    },
  });
  return (
    <Card className="border-amber-200">
      <CardTitle>Fix the letter</CardTitle>
      <p className="mb-2 text-sm text-slate-600">
        The draft failed its checks twice, so it was not sent. Correct it and send it.
      </p>
      {l.issues.length > 0 && (
        <ul className="mb-3 list-disc pl-5 text-xs text-rose-600">
          {l.issues.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      )}
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Field label="Subject">
          <Input value={subject} onChange={(e) => setSubject(e.target.value)} required minLength={3} />
        </Field>
        <Field label="Letter" hint="Final text: write the actual amounts and names, no {PLACEHOLDERS}.">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={12}
            required
            minLength={40}
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
          />
        </Field>
        <ErrorNote error={m.error} />
        <Button type="submit" className="w-full" disabled={m.isPending || m.isSuccess}>
          {m.isSuccess ? "Sent" : m.isPending ? "Sending…" : "Send letter"}
        </Button>
      </form>
    </Card>
  );
}

function Assignment({ claimId, assignedTo }: { claimId: string; assignedTo: string | null }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const mine = assignedTo === user?.email;
  const m = useMutation({
    mutationFn: (to: "me" | null) => api.assign(claimId, to),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["claim", claimId] });
      qc.invalidateQueries({ queryKey: ["claims"] });
    },
  });
  return (
    <Card className="py-3">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span>
          {assignedTo ? (
            <>
              Assigned to <strong>{mine ? "you" : assignedTo}</strong>
            </>
          ) : (
            <span className="text-slate-500">Unassigned: in the shared queue</span>
          )}
        </span>
        {!assignedTo && (
          <Button variant="secondary" onClick={() => m.mutate("me")} disabled={m.isPending}>
            Assign to me
          </Button>
        )}
        {mine && (
          <Button variant="ghost" onClick={() => m.mutate(null)} disabled={m.isPending}>
            Release
          </Button>
        )}
      </div>
      {assignedTo && !mine && <p className="mt-2 text-xs text-slate-500">Only the assignee can act on this claim.</p>}
      <ErrorNote error={m.error} />
    </Card>
  );
}
