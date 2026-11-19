import type { StreamEvent } from "../lib/useClaimEvents";
import { Spinner, cn } from "./ui";

// Top-level graph nodes in run order, with claimant-friendly wording.
const STEPS: { node: string; label: string }[] = [
  { node: "intake", label: "Reading your claim and photos" },
  { node: "register_core", label: "Registering the claim" },
  { node: "coverage", label: "Checking your policy" },
  { node: "fraud", label: "Verifying the details" },
  { node: "payout", label: "Calculating the payment" },
  { node: "review", label: "Adjuster review" },
  { node: "letter", label: "Writing your decision letter" },
  { node: "close", label: "Done" },
];

type State = "todo" | "running" | "done" | "skipped" | "waiting";

export function stepStates(events: StreamEvent[]): Record<string, State> {
  const s: Record<string, State> = Object.fromEntries(STEPS.map((x) => [x.node, "todo"]));
  for (const e of events) {
    const node = e.name === "human_review" || e.name === "auto_approve" ? "review" : e.name;
    if (!(node in s)) continue; // sub-steps (e.g. "coverage/assess") are not shown here
    if (e.kind === "node_start") s[node] = "running";
    if (e.kind === "node_end") s[node] = e.name === "auto_approve" ? "skipped" : "done";
  }
  const status = [...events].reverse().find((e) => e.kind === "status")?.name;
  if (status === "pending_review") s.review = "waiting";
  if (events.some((e) => e.kind === "human")) s.review = "done";
  // the review gate's node starts and then pauses; a human event or the letter resolves it
  if (s.review === "running" && s.letter !== "todo") s.review = "done";
  return s;
}

export function ProgressSteps({ events, finalStatus }: { events: StreamEvent[]; finalStatus?: string }) {
  const states = stepStates(events);
  const failed = finalStatus === "failed" || finalStatus === "invalid";
  return (
    <ol className="space-y-2">
      {STEPS.map(({ node, label }) => {
        const st = states[node];
        const text =
          node === "review" && st === "skipped"
            ? "Approved automatically (within auto-approval limits)"
            : node === "review" && st === "waiting"
              ? "Waiting for an adjuster"
              : label;
        return (
          <li key={node} className="flex items-center gap-3 text-sm">
            <span
              className={cn(
                "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-bold",
                (st === "done" || st === "skipped") && "bg-emerald-500 text-white",
                st === "running" && "text-blue-600",
                st === "waiting" && "bg-amber-400 text-white",
                st === "todo" && "bg-slate-200",
              )}
            >
              {st === "done" || st === "skipped" ? "✓" : st === "running" && !failed ? <Spinner /> : st === "waiting" ? "…" : ""}
            </span>
            <span className={cn(st === "todo" && "text-slate-400")}>{text}</span>
          </li>
        );
      })}
    </ol>
  );
}
