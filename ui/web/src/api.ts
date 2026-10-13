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

/* The access token lives in sessionStorage: it dies with the tab and is never sent anywhere but /api. */
const TOKEN_KEY = "pm_token";
let unauthorized: () => void = () => {};

export const auth = {
  hasToken: (): boolean => {
    try {
      return !!sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return false;
    }
  },
  clear: (): void => {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable */
    }
  },
  /** Called once when the API rejects the token (expired or revoked), so the UI can ask to sign in again. */
  onUnauthorized: (fn: () => void): void => {
    unauthorized = fn;
  },
  /** False when the server runs with auth disabled (local demo): there is no login to show. */
  required: async (): Promise<boolean> => {
    try {
      const r = await fetch("/api/health");
      return ((await r.json()) as { auth_required?: boolean }).auth_required !== false;
    } catch {
      return true;
    }
  },
  login: async (username: string, password: string): Promise<void> => {
    const out = await call<{ access_token: string }>("POST", "/api/login", { username, password });
    sessionStorage.setItem(TOKEN_KEY, out!.access_token);
  },
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = path === "/api/login" ? null : sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/api/login") {
    auth.clear();
    unauthorized();
  }
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
