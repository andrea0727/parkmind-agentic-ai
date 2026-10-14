import { useSyncExternalStore } from "react";

/* Dev tools: an in-memory log of every /api call, recorded only while debug is on. */

export interface ApiCall {
  id: number;
  at: number;
  method: string;
  path: string;
  status: number | null;
  ms: number;
  request?: unknown;
  response?: unknown;
}

interface DebugState {
  enabled: boolean;
  calls: ApiCall[];
}

const KEY = "pm_debug";
const MAX_CALLS = 50;
const SECRET_KEYS = new Set(["password", "access_token", "token"]);

function readEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

let state: DebugState = { enabled: readEnabled(), calls: [] };
let seq = 0;
const subs = new Set<() => void>();

function set(next: DebugState) {
  state = next;
  subs.forEach((fn) => fn());
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, SECRET_KEYS.has(k) ? "***" : redact(v)]),
    );
  }
  return value;
}

export const debug = {
  isEnabled: (): boolean => state.enabled,
  setEnabled: (on: boolean): void => {
    try {
      localStorage.setItem(KEY, on ? "1" : "0");
    } catch {
      /* storage unavailable: the toggle still works for this tab */
    }
    set({ ...state, enabled: on });
  },
  clear: (): void => set({ ...state, calls: [] }),
  record: (call: Omit<ApiCall, "id" | "at">): void => {
    if (!state.enabled) return;
    const entry: ApiCall = {
      ...call,
      id: ++seq,
      at: Date.now(),
      request: redact(call.request),
      response: redact(call.response),
    };
    set({ ...state, calls: [entry, ...state.calls].slice(0, MAX_CALLS) });
  },
};

const subscribe = (fn: () => void) => {
  subs.add(fn);
  return () => {
    subs.delete(fn);
  };
};

export const useDebug = (): DebugState => useSyncExternalStore(subscribe, () => state);
