import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type Proposal, type Turn } from "./api";
import { FIXTURE } from "./fixture";
import { HUB, MAP_H, MAP_W, allPointsFor, applyDiff, project } from "./geo";
import type { Lang, LocFn, TFn } from "./i18n";
import { markChanged, planFromApi, proposalFromApi } from "./liveMap";
import { mockReply, replyAfterConfirm } from "./mockReply";
import type { ChipView, PlanEvent, ProposalView, Reply } from "./types";

export type Screen = "home" | "plan" | "park" | "profile" | "more" | "eval";
export type Mode = "mock" | "live";
export type ProposalState = "none" | "pending" | "applied" | "kept";
export type Filter = "all" | "plan" | "access" | "open";

export type ChatItem =
  | { id: number; k: "me"; text: string }
  | { id: number; k: "bot"; text: string; chips?: ChipView[]; preview?: boolean }
  | { id: number; k: "meta"; text: string }
  | { id: number; k: "typing" }
  | { id: number; k: "suggest"; ids: number[]; label: string }
  | { id: number; k: "confirm"; chips: ChipView[]; consentRequired: boolean }
  | { id: number; k: "evconfirm"; text: string };

export interface MapState {
  zoom: number;
  cx: number;
  cy: number;
  filter: Filter;
  selected: string | null;
}

export interface State {
  mode: Mode;
  booting: boolean;
  threadId: string | null;
  plan: PlanEvent[];
  planStatus: "valid" | "updated";
  proposal: ProposalView | null;
  proposalState: ProposalState;
  screen: Screen;
  dismissed: string[];
  read: string[];
  sheet: boolean;
  psheet: boolean;
  nsheet: boolean;
  lsheet: boolean;
  sessbar: boolean;
  seeded: boolean;
  chat: ChatItem[];
  thinking: boolean;
  toast: string | null;
  map: MapState;
  awaitingCorrection: boolean;
}

export interface Notif {
  id: string;
  kind: string;
  icon: string;
  mins: number;
  title: string;
  body: string;
  action: "review" | null;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const MAP_HOME: Pick<MapState, "zoom" | "cx" | "cy"> = { zoom: 1, cx: 0.5, cy: 0.5 };
const POLL_MS = 8000;
const SESSION_MS = 75000;
const HARD_RE = /wheelchair|stroller|accessib|mobility|height|\bcm\b|depart|step-free|sensory/i;

function initial(): State {
  return {
    mode: "mock",
    booting: true,
    threadId: null,
    plan: structuredClone(FIXTURE.plan.events),
    planStatus: "valid",
    proposal: structuredClone(FIXTURE.proposal),
    proposalState: "pending",
    screen: "home",
    dismissed: [],
    read: [],
    sheet: false,
    psheet: false,
    nsheet: false,
    lsheet: false,
    sessbar: false,
    seeded: true,
    chat: [],
    thinking: false,
    toast: null,
    map: { ...MAP_HOME, filter: "all", selected: null },
    awaitingCorrection: false,
  };
}

function liveBlank(threadId: string): State {
  return {
    ...initial(),
    mode: "live",
    booting: false,
    threadId,
    plan: [],
    proposal: null,
    proposalState: "none",
  };
}

export function notificationsOf(s: State, t: TFn): Notif[] {
  const list: Notif[] = FIXTURE.alerts
    .filter((a) => s.mode === "mock" && !s.dismissed.includes(a.id))
    .map((a) => ({
      id: a.id,
      kind: a.kind,
      icon: a.icon,
      mins: a.mins,
      title: t(a.title_key),
      body: t(a.body_key),
      action: null,
    }));
  if (s.proposalState === "pending")
    list.unshift({
      id: "n_proposal",
      kind: "change",
      icon: "🌧️",
      mins: 0,
      title: t("change_title"),
      body: t("change_body"),
      action: "review",
    });
  return list;
}

export function useController(lang: Lang, t: TFn, loc: LocFn) {
  const [s, setS] = useState<State>(initial);
  const ref = useRef<State>(s);
  const ids = useRef(1);
  const toastTimer = useRef<number>();
  const sessTimer = useRef<number>();
  const seen = useRef<Set<string>>(new Set());
  const env = useRef({ lang, t, loc });
  env.current = { lang, t, loc };

  /* Async chat flows read ref.current so they never act on a stale render. */
  const upd = useCallback((fn: (st: State) => Partial<State>) => {
    ref.current = { ...ref.current, ...fn(ref.current) };
    setS(ref.current);
  }, []);

  const toast = useCallback(
    (msg: string) => {
      upd(() => ({ toast: msg }));
      window.clearTimeout(toastTimer.current);
      toastTimer.current = window.setTimeout(() => upd(() => ({ toast: null })), 3000);
    },
    [upd],
  );

  /* ---------------- chat helpers ---------------- */
  const push = useCallback(
    (item: DistributiveOmit<ChatItem, "id">): number => {
      const id = ids.current++;
      upd((st) => ({ chat: [...st.chat, { ...item, id } as ChatItem] }));
      return id;
    },
    [upd],
  );
  const drop = useCallback((id: number) => upd((st) => ({ chat: st.chat.filter((c) => c.id !== id) })), [upd]);

  const armSessionTimer = useCallback(() => {
    window.clearTimeout(sessTimer.current);
    upd(() => ({ sessbar: false }));
    sessTimer.current = window.setTimeout(() => {
      if (ref.current.sheet) upd(() => ({ sessbar: true }));
    }, SESSION_MS);
  }, [upd]);

  /* ---------------- overlays ---------------- */
  const openProposal = useCallback(() => upd(() => ({ psheet: true })), [upd]);
  const closeProposal = useCallback(() => upd(() => ({ psheet: false })), [upd]);
  const openNotifs = useCallback(() => upd(() => ({ nsheet: true })), [upd]);
  const closeNotifs = useCallback(() => upd(() => ({ nsheet: false })), [upd]);
  const openLang = useCallback(() => upd(() => ({ lsheet: true })), [upd]);
  const closeLang = useCallback(() => upd(() => ({ lsheet: false })), [upd]);
  const openSheet = useCallback(() => {
    upd(() => ({ sheet: true }));
    armSessionTimer();
  }, [upd, armSessionTimer]);
  const closeSheet = useCallback(() => {
    window.clearTimeout(sessTimer.current);
    upd(() => ({ sheet: false, sessbar: false }));
  }, [upd]);
  const dismissSess = useCallback(() => armSessionTimer(), [armSessionTimer]);

  const closeTop = useCallback(() => {
    const st = ref.current;
    if (st.psheet) closeProposal();
    else if (st.lsheet) closeLang();
    else if (st.nsheet) closeNotifs();
    else closeSheet();
  }, [closeProposal, closeLang, closeNotifs, closeSheet]);

  const closeAll = useCallback(() => {
    window.clearTimeout(sessTimer.current);
    upd(() => ({ psheet: false, lsheet: false, nsheet: false, sheet: false, sessbar: false }));
  }, [upd]);

  const go = useCallback(
    (screen: Screen | "assistant") => {
      if (screen === "assistant") return openSheet();
      upd(() => ({ screen }));
    },
    [upd, openSheet],
  );

  /* ---------------- proposals ---------------- */
  const ingestProposal = useCallback(
    (p: Proposal): boolean => {
      if (seen.current.has(p.proposal_id)) return false;
      seen.current.add(p.proposal_id);
      const view = proposalFromApi(p, ref.current.plan);
      upd(() => ({ proposal: view, proposalState: "pending" }));
      return true;
    },
    [upd],
  );

  const applyProposal = useCallback(async () => {
    const st = ref.current;
    const p = st.proposal;
    if (!p) return;
    const { t: tt } = env.current;
    try {
      if (st.mode === "live" && st.threadId) {
        await api.decide(st.threadId, p.proposal_id, "approve");
        const fresh = await api.plan(st.threadId);
        const events = fresh ? markChanged(planFromApi(fresh), p) : st.plan;
        upd(() => ({ plan: events, proposalState: "applied", planStatus: "updated", psheet: false, screen: "plan" }));
        toast(tt("applied", { n: events.length }));
      } else {
        const events = applyDiff(st.plan, p);
        upd(() => ({ plan: events, proposalState: "applied", planStatus: "updated", psheet: false, screen: "plan" }));
        toast(tt("applied", { n: events.length }));
      }
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e));
    }
  }, [upd, toast]);

  const keepPlan = useCallback(async () => {
    const st = ref.current;
    const p = st.proposal;
    if (!p) return;
    try {
      if (st.mode === "live" && st.threadId) await api.decide(st.threadId, p.proposal_id, "reject");
      upd(() => ({ proposalState: "kept", psheet: false }));
      toast(env.current.t("kept_toast"));
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e));
    }
  }, [upd, toast]);

  /* ---------------- chat delivery ---------------- */
  const suggest = useCallback(
    (order: number[], label: string) => push({ k: "suggest", ids: order, label }),
    [push],
  );

  const deliver = useCallback(
    async (r: Reply) => {
      const { t: tt } = env.current;
      push({ k: "bot", text: r.text, chips: r.constraints });

      if (r.confirm) {
        await sleep(420);
        push({
          k: "confirm",
          chips: (r.constraints ?? []).filter((c) => c.kind === "hard"),
          consentRequired: r.consentRequired ?? true,
        });
        return;
      }
      if (r.eventConfirm) {
        await sleep(300);
        push({ k: "evconfirm", text: tt("ev_confirm") });
        return;
      }
      if (r.follow || r.plan_preview) {
        await sleep(480);
        push({ k: "bot", text: r.follow ?? "", preview: !!r.plan_preview });
      }
      if (r.open_proposal) {
        await sleep(450);
        push({ k: "meta", text: tt("proposed") });
        openProposal();
        return;
      }
      if (r.followups) {
        await sleep(550);
        suggest([2, 5, 7, 9, 4, 8, 3, 6], "anything_else");
      }
    },
    [push, openProposal, suggest],
  );

  const turnToReply = useCallback(
    (turn: Turn): Reply => {
      const text = [...turn.messages, turn.status === "needs_answer" ? turn.question : null]
        .filter(Boolean)
        .join("\n\n");
      if (turn.error) return { text: `⚠ ${turn.error}` };
      switch (turn.status) {
        case "needs_confirmation": {
          const c = turn.confirmation;
          const chips: ChipView[] = (c?.echo ?? []).map((line) => ({
            kind: HARD_RE.test(line) ? "hard" : "soft",
            label: line,
          }));
          return { text, constraints: chips, confirm: true, consentRequired: c ? c.consent_required || c.accessibility_guests.length > 0 : true };
        }
        case "needs_approval":
          return { text, open_proposal: true };
        case "needs_event_confirmation":
          return { text, eventConfirm: true };
        case "needs_answer":
          return { text };
        default:
          return { text, followups: true };
      }
    },
    [],
  );

  const deliverTurn = useCallback(
    async (turn: Turn) => {
      if (turn.status === "needs_approval" && turn.proposal) ingestProposal(turn.proposal);
      if (turn.status === "done") {
        const fresh = ref.current.threadId ? await api.plan(ref.current.threadId).catch(() => null) : null;
        if (fresh) upd(() => ({ plan: planFromApi(fresh), planStatus: "valid" }));
      }
      const reply = turnToReply(turn);
      if (turn.status === "done" && ref.current.plan.length) reply.plan_preview = true;
      await deliver(reply);
    },
    [ingestProposal, turnToReply, deliver, upd],
  );

  const failBubble = useCallback(
    (e: unknown) => push({ k: "bot", text: `⚠ ${e instanceof ApiError ? e.message : String(e)}` }),
    [push],
  );

  const send = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      const { lang: L, t: tt, loc: ll } = env.current;
      armSessionTimer();
      upd(() => ({ seeded: false, thinking: true }));
      push({ k: "me", text });
      const ty = push({ k: "typing" });
      const st = ref.current;

      if (st.mode === "live" && st.threadId) {
        try {
          let turn: Turn;
          if (st.awaitingCorrection) {
            upd(() => ({ awaitingCorrection: false }));
            turn = await api.confirmConstraints(st.threadId, { confirmed: false, consent: false, correction: text });
          } else {
            turn = await api.say(st.threadId, text, L);
          }
          drop(ty);
          upd(() => ({ thinking: false }));
          await deliverTurn(turn);
        } catch (e) {
          drop(ty);
          upd(() => ({ thinking: false }));
          failBubble(e);
        }
        return;
      }

      const r = mockReply(text, { lang: L, t: tt, loc: ll, plan: st.plan });
      await sleep(800);
      drop(ty);
      upd(() => ({ thinking: false }));
      await deliver(r);
    },
    [armSessionTimer, upd, push, drop, deliverTurn, failBubble, deliver],
  );

  const pickSuggestion = useCallback(
    (chatId: number, title: string) => {
      drop(chatId);
      void send(title);
    },
    [drop, send],
  );

  const dismissSeed = useCallback(() => upd(() => ({ seeded: false })), [upd]);

  const confirmOk = useCallback(
    async (chatId: number, consent: boolean, retention: "session" | "profile") => {
      const { t: tt, lang: L } = env.current;
      drop(chatId);
      push({ k: "meta", text: tt("cf_done", { r: retention === "session" ? tt("cf_ret_s") : tt("cf_ret_k") }) });
      const ty = push({ k: "typing" });
      const st = ref.current;
      if (st.mode === "live" && st.threadId) {
        try {
          const turn = await api.confirmConstraints(st.threadId, {
            confirmed: true,
            consent,
            retention_policy: retention === "session" ? "session_only" : "persisted",
          });
          drop(ty);
          await deliverTurn(turn);
        } catch (e) {
          drop(ty);
          failBubble(e);
        }
        return;
      }
      await sleep(700);
      drop(ty);
      await deliver(replyAfterConfirm(L));
    },
    [drop, push, deliverTurn, failBubble, deliver],
  );

  const confirmEdit = useCallback(
    (chatId: number) => {
      drop(chatId);
      upd(() => ({ awaitingCorrection: ref.current.mode === "live" }));
    },
    [drop, upd],
  );

  const eventConfirm = useCallback(
    async (chatId: number, confirmed: boolean) => {
      drop(chatId);
      const st = ref.current;
      if (!st.threadId) return;
      const ty = push({ k: "typing" });
      try {
        const turn = await api.confirmEvents(st.threadId, confirmed);
        drop(ty);
        await deliverTurn(turn);
      } catch (e) {
        drop(ty);
        failBubble(e);
      }
    },
    [drop, push, deliverTurn, failBubble],
  );

  /* ---------------- notifications ---------------- */
  const markRead = useCallback(
    (id: string) => upd((st) => ({ read: st.read.includes(id) ? st.read : [...st.read, id] })),
    [upd],
  );
  const dismissAlert = useCallback(
    (id: string) => upd((st) => ({ dismissed: [...st.dismissed, id] })),
    [upd],
  );
  const markAllRead = useCallback(() => {
    const ids2 = notificationsOf(ref.current, env.current.t).map((n) => n.id);
    upd((st) => ({ read: Array.from(new Set([...st.read, ...ids2])) }));
  }, [upd]);
  const reviewProposal = useCallback(() => {
    markRead("n_proposal");
    closeNotifs();
    openProposal();
  }, [markRead, closeNotifs, openProposal]);

  /* ---------------- map ---------------- */
  const setMap = useCallback(
    (patch: Partial<MapState>) =>
      upd((st) => {
        const m = { ...st.map, ...patch };
        m.zoom = Math.min(6, Math.max(1, m.zoom));
        const edge = 0.5 / m.zoom;
        m.cx = Math.min(1 - edge, Math.max(edge, m.cx));
        m.cy = Math.min(1 - edge, Math.max(edge, m.cy));
        return { map: m };
      }),
    [upd],
  );
  const zoomBy = useCallback((f: number) => setMap({ zoom: ref.current.map.zoom * f }), [setMap]);
  const here = useCallback(() => {
    const st = ref.current;
    const cur = st.plan.find((e) => e.state === "current") ?? st.plan[0];
    return cur?.location ?? HUB;
  }, []);
  const zoomToMe = useCallback(() => {
    const p = project(allPointsFor(FIXTURE.rides), MAP_W, MAP_H, 16)(here());
    setMap({ zoom: 3, cx: p.x / MAP_W, cy: p.y / MAP_H });
  }, [here, setMap]);

  /* ---------------- mode / session ---------------- */
  const goLive = useCallback(async (): Promise<boolean> => {
    try {
      const threadId = await api.startSession();
      seen.current = new Set();
      ref.current = { ...liveBlank(threadId), screen: ref.current.screen };
      setS(ref.current);
      return true;
    } catch {
      return false;
    }
  }, []);

  const goMock = useCallback(() => {
    seen.current = new Set();
    window.clearTimeout(sessTimer.current);
    ref.current = { ...initial(), booting: false, screen: ref.current.screen };
    setS(ref.current);
  }, []);

  const setMode = useCallback(
    async (mode: Mode) => {
      if (mode === ref.current.mode) return;
      if (mode === "mock") return goMock();
      if (!(await goLive())) toast(env.current.t("live_fail"));
      else toast(env.current.t("live_on"));
    },
    [goLive, goMock, toast],
  );

  const reset = useCallback(async () => {
    window.clearTimeout(sessTimer.current);
    const wasLive = ref.current.mode === "live";
    if (wasLive && (await goLive())) {
      /* fresh thread, empty plan */
    } else {
      goMock();
    }
    ref.current = { ...ref.current, screen: "home" };
    setS(ref.current);
    toast(env.current.t("reset_toast"));
  }, [goLive, goMock, toast]);

  /* Connect on load: a reachable API means live mode with a real thread_id. */
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const ok = await goLive();
      if (!cancelled && !ok) upd(() => ({ booting: false }));
    })();
    return () => {
      cancelled = true;
    };
  }, [goLive, upd]);

  /* Poll for a proposal the planner raised on its own (event monitor, weather). 204 is ignored. */
  useEffect(() => {
    if (s.mode !== "live" || !s.threadId) return;
    const threadId = s.threadId;
    const tick = async () => {
      try {
        const p = await api.proposal(threadId);
        if (p && ref.current.threadId === threadId) ingestProposal(p);
      } catch {
        /* a failed poll is retried on the next tick */
      }
    };
    const timer = window.setInterval(tick, POLL_MS);
    return () => window.clearInterval(timer);
  }, [s.mode, s.threadId, ingestProposal]);

  const notifs = useMemo(() => notificationsOf(s, t), [s, t]);

  return {
    s,
    notifs,
    here,
    actions: {
      go, openSheet, closeSheet, openProposal, closeProposal, openNotifs, closeNotifs, openLang, closeLang,
      closeTop, closeAll, dismissSess, armSessionTimer, send, pickSuggestion, dismissSeed, confirmOk, confirmEdit,
      eventConfirm, applyProposal, keepPlan, markRead, dismissAlert, markAllRead, reviewProposal, setMap,
      zoomToMe, zoomBy, setMode, reset, toast,
    },
  };
}

export type Controller = ReturnType<typeof useController>;
type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never;
