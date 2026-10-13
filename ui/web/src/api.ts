// Thin client over the FastAPI layer; the shapes mirror src/parkmind/api/schemas.py.

export type TurnStatus =
  | "needs_answer"
  | "needs_confirmation"
  | "needs_approval"
  | "needs_event_confirmation"
  | "done";

export interface Stop {
  stop_id: string;
  node_id: string;
  kind: string;
  title: string;
  land: string | null;
  arrival: string;
  departure: string;
  wait_minutes: number;
  walking_minutes: number;
  served_guests?: string[];
}

export interface Plan {
  plan_id: string;
  version: number;
  stops: Stop[];
  total_wait_minutes: number;
  total_walking_minutes: number;
  unmet_must_do: string[];
}

export interface DiffEntry {
  stop_id: string;
  title: string;
}

export interface Proposal {
  proposal_id: string;
  trigger: string;
  explanation: string;
  diff: {
    removed: DiffEntry[];
    added: DiffEntry[];
    moved: DiffEntry[];
    modified: (DiffEntry & { changes: Record<string, unknown[]> })[];
  };
  metrics: {
    wait_minutes_before: number | null;
    wait_minutes_after: number;
    walking_minutes_before: number | null;
    walking_minutes_after: number;
  };
  provenance: {
    snapshot_id: string | null;
    retrieved_at: string | null;
    forecast_strategy: string | null;
    optimizer_strategy: string | null;
    constraints_version: number | null;
  };
  plan: Plan;
}

export interface Confirmation {
  pending: string[];
  echo: string[];
  accessibility_guests: string[];
  consent_required: boolean;
}

export interface HeldEvent {
  event_id: string;
  type: string;
  attraction_id: string | null;
}

export interface Turn {
  thread_id: string;
  status: TurnStatus;
  messages: string[];
  missing: string[];
  question: string | null;
  confirmation: Confirmation | null;
  proposal: Proposal | null;
  held_events: HeldEvent[];
  error: string | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
    throw new ApiError(response.status, detail ?? `HTTP ${response.status}`);
  }
  return data as T;
}

const q = (threadId: string) => `thread_id=${encodeURIComponent(threadId)}`;

export const api = {
  startSession: async () => (await call<{ thread_id: string }>("POST", "/api/session"))!.thread_id,
  say: async (t: string, text: string, lang: string) =>
    (await call<Turn>("POST", `/api/message?${q(t)}`, { text, lang }))!,
  confirmConstraints: async (t: string, body: { confirmed: boolean; consent: boolean; retention_policy?: "session_only" | "persisted"; correction?: string }) =>
    (await call<Turn>("POST", `/api/constraints/confirm?${q(t)}`, body))!,
  proposal: (t: string) => call<Proposal>("GET", `/api/proposal?${q(t)}`),
  plan: (t: string) => call<Plan>("GET", `/api/plan?${q(t)}`),
  decide: async (t: string, id: string, decision: "approve" | "reject", reason?: string) =>
    (await call<Turn>("POST", `/api/proposal/${encodeURIComponent(id)}/${decision}?${q(t)}`, reason ? { reason } : {}))!,
  reportEvent: async (t: string, event: Record<string, unknown>) =>
    (await call<Turn>("POST", `/api/events?${q(t)}`, event))!,
  confirmEvents: async (t: string, confirmed: boolean) =>
    (await call<Turn>("POST", `/api/events/confirm?${q(t)}`, { confirmed }))!,
};
