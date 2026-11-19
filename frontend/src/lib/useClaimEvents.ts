import { useEffect, useState } from "react";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_URL, getToken, type AuditEvent } from "./api";

// Live progress over SSE. EventSource cannot send an Authorization header,
// so a fetch-based client is used. The server replays history first, then streams live.
export type StreamEvent = Pick<AuditEvent, "kind" | "name" | "ts"> & Partial<AuditEvent>;

// The server ends the stream when the claim pauses (pending_review) or finishes; pass the
// claim's current status as `epoch` so the hook reconnects (and replays) when it moves on.
export function useClaimEvents(
  claimId: string | undefined,
  onEvent?: (e: StreamEvent) => void,
  epoch?: string,
) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!claimId) return;
    setEvents([]);
    setDone(false);
    const ctrl = new AbortController();
    fetchEventSource(`${API_URL}/claims/${claimId}/events`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      signal: ctrl.signal,
      openWhenHidden: true,
      onmessage(msg) {
        if (!msg.data) return; // keep-alive ping
        const e = JSON.parse(msg.data) as StreamEvent;
        setEvents((prev) => [...prev, e]);
        onEvent?.(e);
      },
      onclose() {
        setDone(true);
        throw new Error("closed"); // stop: the server closes once the claim settles
      },
      onerror(err) {
        setDone(true);
        throw err; // no automatic retry loop
      },
    }).catch(() => setDone(true));
    return () => ctrl.abort();
    // onEvent intentionally excluded: callers pass inline callbacks
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimId, epoch]);

  return { events, done };
}
