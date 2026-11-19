// Typed client for the ClaimPilot API. Types mirror the backend's pydantic models.

export const API_URL: string = import.meta.env.VITE_API_URL ?? "/api";
const TOKEN_KEY = "claimpilot.token";

export type Role = "claimant" | "adjuster" | "auditor";
export type ClaimStatus =
  | "submitted"
  | "processing"
  | "pending_review"
  | "closed"
  | "invalid"
  | "failed";
export type Decision = "approved" | "auto_approved" | "denied";

export interface Me {
  id: number;
  email: string;
  name: string;
  role: Role;
  policy_number: string | null;
}

export interface ClaimSummary {
  claim_id: string;
  policy_number: string;
  status: ClaimStatus;
  decision: Decision | null;
  final_payout: number | null;
  created_at: string;
  updated_at: string;
  // staff only
  review_reasons?: string[];
  core_claim_id?: string | null;
  processing_ms?: number | null;
  error?: string | null;
}

export interface Letter {
  subject: string;
  body: string;
  revisions: number;
  issues: string[];
  needs_adjuster_edit: boolean;
}

export interface Citation {
  clause_id: string;
  quote: string;
}

export interface FraudCheck {
  name: string;
  risk: number;
  findings: string[];
  started_at: string;
  finished_at: string;
}

export interface PayoutStep {
  label: string;
  amount: number;
  clause_id: string | null;
  note: string | null;
}

export interface Quote {
  shop: string;
  total: number;
  line_items: { damage_area: string; description: string; parts: number; labor: number; total: number }[];
}

export interface ReviewDecision {
  action: "approve" | "deny" | "override";
  reason?: string | null;
  payout_amount?: number | null;
  adjuster?: string;
}

export interface ClaimRun {
  claim_id: string;
  status: ClaimStatus;
  decision: Decision | null;
  final_payout: number | null;
  core_claim_id: string | null;
  intake: {
    errors: string[];
    flags: string[];
    policy: Record<string, unknown> | null;
    extraction: {
      incident_date: string | null;
      location: string | null;
      vehicle: { make?: string; model?: string; color?: string } | null;
      damage_areas: string[];
      described_cause: string | null;
      claimed_amount: number | null;
      claim_type: string | null;
      severity: string | null;
      confidence: Record<string, number>;
    } | null;
    low_confidence_fields: string[];
  } | null;
  coverage: {
    decision: string;
    coverage_part: string | null;
    deductible: number | null;
    limit: number | null;
    citations: Citation[];
    reasoning: string;
    flags: string[];
  } | null;
  fraud: { score: number; reasons: string[]; checks: FraudCheck[] } | null;
  payout: {
    vehicle_value: { acv: number; source: string } | null;
    estimates: { quotes: Quote[]; lowest_total: number | null; unpriced_areas: string[] } | null;
    result: { basis: string; payout: number; total_loss: boolean; steps: PayoutStep[] } | null;
    flags: string[];
  } | null;
  review_reasons: string[];
  review: ReviewDecision | null;
  letter: Letter | null;
  error: string | null;
}

export interface ClaimView extends ClaimSummary {
  detail?: ClaimRun | null; // staff
  letter?: Letter | null; // claimant
  errors?: string[]; // claimant
}

export interface AuditEvent {
  ts: string;
  kind: "status" | "node_start" | "node_end" | "llm" | "tool" | "human";
  name: string;
  actor: string;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  error: string | null;
  data: Record<string, unknown>;
}

export interface Metrics {
  claims_total: number;
  by_status: Record<string, number>;
  decided: number;
  straight_through_rate: number | null;
  escalation_rate: number | null;
  escalation_by_reason: Record<string, number>;
  processing_ms_p50: number | null;
  processing_ms_p95: number | null;
  llm_cost_per_claim_usd: number | null;
  tokens_per_claim: number | null;
  manual_handling_minutes_assumption: number;
  est_handling_hours_saved: number;
}

export interface ClaimFormInput {
  policy_number: string;
  incident_date: string;
  incident_location: string;
  description: string;
  claimed_amount?: number | null;
}

// Token storage is a per-browser convenience; the app works without it.
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lasts until reload */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        [d.loc?.filter((l) => l !== "body").join("."), d.msg].filter(Boolean).join(": "),
      )
      .join("; ");
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const DOWN = "Can't reach the ClaimPilot API. Is the backend running on port 8000?";
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, DOWN);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null; // non-JSON reply, e.g. the dev proxy's error page when the API is down
  }
  if (!res.ok) {
    // the Vite proxy answers 500/502/504 with no JSON when the backend isn't up
    if (res.status >= 500 && body === null) throw new ApiError(res.status, DOWN);
    if (res.status === 401 && token) {
      setToken(null);
      window.dispatchEvent(new Event("claimpilot:logout"));
    }
    throw new ApiError(res.status, errorMessage(body, res.statusText));
  }
  return body as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; role: Role; name: string }>("/auth/login", {
      method: "POST",
      body: new URLSearchParams({ username: email, password }),
    }),
  me: () => request<Me>("/auth/me"),
  claims: (status?: string) =>
    request<ClaimSummary[]>(`/claims${status ? `?status_filter=${status}` : ""}`),
  claim: (id: string) => request<ClaimView>(`/claims/${id}`),
  audit: (id: string) => request<AuditEvent[]>(`/claims/${id}/audit`),
  metrics: () => request<Metrics>("/metrics"),
  submit: (form: ClaimFormInput, photos: File[]) => {
    const fd = new FormData();
    fd.set("form", JSON.stringify(form));
    photos.forEach((p) => fd.append("photos", p));
    return request<{ claim_id: string }>("/claims", { method: "POST", body: fd });
  },
  review: (id: string, decision: ReviewDecision) =>
    request<{ status: string }>(`/claims/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(decision),
    }),
};
