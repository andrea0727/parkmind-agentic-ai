// Thin client over the FastAPI layer; the shapes mirror src/parkmind/api/schemas.py.
import { debug } from "./debug";

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
  base_plan_id: string;
  candidate_plan_id: string;
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
  provisional_offer: boolean;
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
  /** Display name of the signed-in user: the JWT subject, capitalised. Null when auth is off or unreadable. */
  username: (): string | null => {
    try {
      const token = sessionStorage.getItem(TOKEN_KEY);
      if (!token) return null;
      const body = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      const sub = (JSON.parse(atob(body)) as { sub?: string }).sub;
      return sub ? sub.charAt(0).toUpperCase() + sub.slice(1) : null;
    } catch {
      return null;
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

/* A poll that keeps returning nothing (204) or the same body is not worth a log line. */
let lastQuiet = "";

async function call<T>(method: string, path: string, body?: unknown, quiet = false): Promise<T | null> {
  const started = performance.now();
  let status: number | null = null;
  let payload: unknown;
  try {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const token = path === "/api/login" ? null : sessionStorage.getItem(TOKEN_KEY);
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    status = response.status;
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    payload = data;
    if (response.status === 401 && path !== "/api/login") {
      auth.clear();
      unauthorized();
    }
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
      throw new ApiError(response.status, detail ?? `HTTP ${response.status}`);
    }
    return data as T;
  } catch (e) {
    if (status === null) payload = { network_error: String(e) };
    throw e;
  } finally {
    const key = `${status}:${JSON.stringify(payload ?? null)}`;
    if (!quiet || key !== lastQuiet) {
      debug.record({ method, path, status, ms: Math.round(performance.now() - started), request: body, response: payload });
    }
    if (quiet) lastQuiet = key;
  }
}

const q = (threadId: string) => `thread_id=${encodeURIComponent(threadId)}`;

export const api = {
  startSession: async () =>
    (await call<{ thread_id: string; park: { closed: boolean; closing_time: string } | null }>("POST", "/api/session"))!,
  say: async (t: string, text: string, lang: string) =>
    (await call<Turn>("POST", `/api/message?${q(t)}`, { text, lang }))!,
  confirmConstraints: async (t: string, body: { confirmed: boolean; consent: boolean; correction?: string }) =>
    (await call<Turn>("POST", `/api/constraints/confirm?${q(t)}`, body))!,
  proposal: (t: string) => call<Proposal>("GET", `/api/proposal?${q(t)}`, undefined, true),
  plan: (t: string) => call<Plan>("GET", `/api/plan?${q(t)}`),
  decide: async (t: string, id: string, decision: "approve" | "reject", reason?: string) =>
    (await call<Turn>("POST", `/api/proposal/${encodeURIComponent(id)}/${decision}?${q(t)}`, reason ? { reason } : {}))!,
  reportEvent: async (t: string, event: Record<string, unknown>) =>
    (await call<Turn>("POST", `/api/events?${q(t)}`, event))!,
  provisional: async (t: string) => (await call<Turn>("POST", `/api/provisional?${q(t)}`))!,
  confirmEvents: async (t: string, confirmed: boolean) =>
    (await call<Turn>("POST", `/api/events/confirm?${q(t)}`, { confirmed }))!,
};
