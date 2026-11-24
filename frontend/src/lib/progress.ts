import type { StreamEvent } from "./useClaimEvents";

// Top-level graph nodes in run order, with claimant-friendly wording.
export const STEPS: { node: string; label: string }[] = [
  { node: "intake", label: "Reading your claim and photos" },
  { node: "register_core", label: "Registering the claim" },
  { node: "coverage", label: "Checking your policy" },
  { node: "fraud", label: "Verifying the details" },
  { node: "payout", label: "Calculating the payment" },
  { node: "review", label: "Adjuster review" },
  { node: "letter", label: "Writing your decision letter" },
  { node: "close", label: "Done" },
];

export type StepState = "todo" | "running" | "done" | "skipped" | "waiting";

export function stepStates(events: StreamEvent[]): Record<string, StepState> {
  const s: Record<string, StepState> = Object.fromEntries(STEPS.map((x) => [x.node, "todo"]));
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
