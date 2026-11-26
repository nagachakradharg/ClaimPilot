import type { StreamEvent } from "../lib/useClaimEvents";
import { STEPS, stepStates } from "../lib/progress";
import { Spinner } from "./ui";
import { cn } from "../lib/format";


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
              : node === "letter" && st === "waiting"
                ? "A person is checking your letter"
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
