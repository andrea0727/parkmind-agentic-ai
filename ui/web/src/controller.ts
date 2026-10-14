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
  | { id: number; k: "evconfirm"; text: string }
  | { id: number; k: "provisional" };

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
  parkClosedAt: string | null;
  plan: PlanEvent[];
  planStatus: "valid" | "updated" | "provisional";
  provisionalOpensAt: number | null;
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
  inbox: InboxItem[];
}

export interface InboxItem {
  id: string;
  kind: string;
  icon: string;
  at: number;
  titleKey: string;
  bodyKey: string;
  vars?: Record<string, string | number>;
}

export type EventKind = "behind" | "tired" | "wait" | "down";

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

function parkTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/New_York",
  }).format(new Date(iso));
}

function initial(): State {
  return {
    mode: "mock",
    booting: true,
    threadId: null,
    parkClosedAt: null,
    plan: structuredClone(FIXTURE.plan.events),
    planStatus: "valid",
    provisionalOpensAt: null,
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
    inbox: [],
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
  const now = Date.now();
  const list: Notif[] = [
    ...[...s.inbox].reverse().map((i) => ({
      id: i.id,
      kind: i.kind,
      icon: i.icon,
      mins: Math.max(0, Math.floor((now - i.at) / 60000)),
      title: t(i.titleKey, i.vars),
      body: t(i.bodyKey, i.vars),
      action: null,
    })),
    ...FIXTURE.alerts
      .filter((a) => s.mode === "mock")
      .map((a) => ({
        id: a.id,
        kind: a.kind,
        icon: a.icon,
        mins: a.mins,
        title: t(a.title_key),
        body: t(a.body_key),
        action: null,
      })),
  ].filter((n) => !s.dismissed.includes(n.id));
  if (s.proposalState === "pending") {
    const live = s.mode === "live" && s.proposal;
    list.unshift({
      id: "n_proposal",
      kind: "change",
      icon: live && s.proposal?.metrics.initial ? "🗺️" : "🌧️",
      mins: 0,
      title: live && s.proposal?.metrics.initial ? t("n_plan_prop_t") : t("change_title"),
      body: live && s.proposal ? plainOf(s.proposal.trigger) : t("change_body"),
      action: "review",
    });
  }
  return list;
}

const plainOf = (v: string | Partial<Record<Lang, string>>): string =>
  typeof v === "string" ? v : (Object.values(v)[0] ?? "");

export function useController(lang: Lang, t: TFn, loc: LocFn) {
  const [s, setS] = useState<State>(initial);
  const ref = useRef<State>(s);
  const ids = useRef(1);
  const toastTimer = useRef<number>();
  const sessTimer = useRef<number>();
  const seen = useRef<Set<string>>(new Set());
  const deliverTurnRef = useRef<(turn: Turn) => Promise<void>>(async () => {});
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

  /* Inbox entries are deduplicated by id, so a reminder can be re-checked on every tick. */
  const notify = useCallback(
    (item: Omit<InboxItem, "at" | "kind" | "icon"> & { kind?: string; icon?: string }, withToast = true) => {
      if (ref.current.inbox.some((i) => i.id === item.id)) return;
      const full: InboxItem = { kind: "info", icon: "🔔", ...item, at: Date.now() };
      upd((st) => ({ inbox: [...st.inbox, full] }));
      if (withToast) toast(env.current.t(full.titleKey, full.vars));
    },
    [upd, toast],
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
      if (ref.current.sheet && ref.current.mode === "mock") upd(() => ({ sessbar: true }));
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
      if (screen === "assistant") return ref.current.sheet ? closeSheet() : openSheet();
      window.clearTimeout(sessTimer.current);
      upd(() => ({ screen, sheet: false, sessbar: false }));
    },
    [upd, openSheet, closeSheet],
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
    window.clearTimeout(sessTimer.current);
    const st = ref.current;
    const p = st.proposal;
    if (!p) return;
    const { t: tt } = env.current;
    try {
      let events: PlanEvent[];
      if (st.mode === "live" && st.threadId) {
        await api.decide(st.threadId, p.proposal_id, "approve");
        const fresh = await api.plan(st.threadId);
        events = fresh ? markChanged(planFromApi(fresh), p) : st.plan;
      } else {
        events = applyDiff(st.plan, p);
      }
      upd(() => ({ plan: events, proposalState: "applied", planStatus: "updated", provisionalOpensAt: null, psheet: false, nsheet: false, sheet: false, sessbar: false, screen: "plan" }));
      notify(
        {
          id: `plan:${p.proposal_id}`,
          icon: "✅",
          titleKey: st.plan.length === 0 ? "n_plan_ready_t" : "n_plan_upd_t",
          bodyKey: st.plan.length === 0 ? "n_plan_ready_b" : "n_plan_upd_b",
          vars: { n: events.length },
        },
        false,
      );
      toast(tt("applied", { n: events.length }));
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e));
    }
  }, [upd, toast, notify]);

  const keepPlan = useCallback(async () => {
    const st = ref.current;
    const p = st.proposal;
    if (!p) return;
    const live = st.mode === "live" && !!st.threadId;
    try {
      const turn = live ? await api.decide(st.threadId!, p.proposal_id, "reject") : null;
      upd(() => ({ proposalState: live ? "none" : "kept", psheet: false, nsheet: false }));
      notify({ id: `kept:${p.proposal_id}`, icon: "👌", titleKey: "n_kept_t", bodyKey: "n_kept_b" }, false);
      toast(env.current.t("kept_toast"));
      if (turn && turn.status !== "done") await deliverTurnRef.current(turn);
      else push({ k: "bot", text: env.current.t("kept_bot") });
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e));
    }
  }, [upd, toast, notify, push]);

  /* ---------------- chat delivery ---------------- */
  const suggest = useCallback(
    (order: number[], label: string) => push({ k: "suggest", ids: order, label }),
    [push],
  );

  const deliver = useCallback(
    async (r: Reply) => {
      const { t: tt } = env.current;
      if (r.text || r.constraints?.length) push({ k: "bot", text: r.text, chips: r.constraints });

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
      if (r.provisionalOffer) {
        await sleep(300);
        push({ k: "provisional" });
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
          if (turn.provisional_offer) return { text, provisionalOffer: true };
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

  deliverTurnRef.current = deliverTurn;

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
    async (chatId: number, consent: boolean) => {
      const { t: tt, lang: L } = env.current;
      drop(chatId);
      push({ k: "meta", text: tt("cf_done") });
      const ty = push({ k: "typing" });
      const st = ref.current;
      if (st.mode === "live" && st.threadId) {
        try {
          const turn = await api.confirmConstraints(st.threadId, {
            confirmed: true,
            consent,
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

  const provisionalPlan = useCallback(
    async (chatId: number) => {
      drop(chatId);
      const st = ref.current;
      if (!st.threadId) return;
      const ty = push({ k: "typing" });
      try {
        const turn = await api.provisional(st.threadId);
        drop(ty);
        push({ k: "bot", text: turn.messages.join("\n\n") });
        if (turn.provisional)
          upd(() => ({
            plan: planFromApi(turn.provisional!.plan),
            planStatus: "provisional",
            provisionalOpensAt: Date.parse(turn.provisional!.opens_at),
            psheet: false, nsheet: false, sheet: false, sessbar: false, screen: "plan",
          }));
      } catch (e) {
        drop(ty);
        failBubble(e);
      }
    },
    [drop, push, failBubble, upd],
  );

  /* The provisional plan refreshes itself once the park opens: the planner re-plans with live data and proposes it. */
  const refreshing = useRef(false);
  useEffect(() => {
    if (s.mode !== "live" || !s.threadId || s.provisionalOpensAt == null) return;
    const threadId = s.threadId;
    const tick = async () => {
      const opensAt = ref.current.provisionalOpensAt;
      if (refreshing.current || opensAt == null || Date.now() < opensAt) return;
      refreshing.current = true;
      try {
        const turn = await api.refreshProvisional(threadId);
        if (ref.current.threadId !== threadId) return;
        if (turn.provisional_offer) return;
        upd(() => ({ provisionalOpensAt: null }));
        await deliverTurnRef.current(turn);
        notify({ id: `prov:${opensAt}`, icon: "🎢", titleKey: "n_prov_t", bodyKey: "n_prov_b" });
      } catch {
        /* not open yet or busy: retried on the next tick */
      } finally {
        refreshing.current = false;
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 30000);
    return () => window.clearInterval(timer);
  }, [s.mode, s.threadId, s.provisionalOpensAt, upd, notify]);
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
      const session = await api.startSession();
      seen.current = new Set();
      ref.current = {
        ...liveBlank(session.thread_id),
        parkClosedAt: session.park?.closed ? parkTime(session.park.closing_time) : null,
        screen: ref.current.screen,
      };
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
      if (!cancelled && !ok) {
        upd(() => ({ booting: false }));
        toast(env.current.t("live_fail"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goLive, upd, toast]);

  /* Poll for a proposal the planner raised on its own (event monitor, weather). 204 is ignored. */
  useEffect(() => {
    if (s.mode !== "live" || !s.threadId) return;
    const threadId = s.threadId;
    const tick = async () => {
      try {
        const p = await api.proposal(threadId);
        if (p && ref.current.threadId === threadId && ingestProposal(p)) toast(env.current.t("change_title"));
      } catch {
        /* a failed poll is retried on the next tick */
      }
    };
    const timer = window.setInterval(tick, POLL_MS);
    return () => window.clearInterval(timer);
  }, [s.mode, s.threadId, ingestProposal, toast]);

  /* Reminders: the next stop within 5 minutes and the end of the plan within 15, from the plan's own times. */
  useEffect(() => {
    if (s.mode !== "live") return;
    const clock = (ms: number) =>
      new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" });
    const tick = () => {
      const plan = ref.current.plan;
      const now = Date.now();
      const next = plan.find((e) => e.startAt != null && e.startAt > now);
      if (next?.startAt && next.startAt - now <= 5 * 60000)
        notify({
          id: `next:${next.event_id}:${next.startAt}`,
          icon: "⏰",
          kind: "urgent",
          titleKey: "n_next_t",
          bodyKey: "n_next_b",
          vars: { n: Math.max(1, Math.ceil((next.startAt - now) / 60000)), title: next.title, time: clock(next.startAt) },
        });
      const last = plan[plan.length - 1];
      if (last?.endAt && last.endAt > now && last.endAt - now <= 15 * 60000)
        notify({
          id: `end:${last.event_id}:${last.endAt}`,
          icon: "🌙",
          titleKey: "n_end_t",
          bodyKey: "n_end_b",
          vars: { title: last.title, time: clock(last.endAt) },
        });
    };
    tick();
    const timer = window.setInterval(tick, 20000);
    return () => window.clearInterval(timer);
  }, [s.mode, s.plan, notify]);

  /* Guest-reported events go through /api/events; the planner decides whether they need a replan. */
  const reportEvent = useCallback(
    async (kind: EventKind, stop?: PlanEvent) => {
      const st = ref.current;
      const { t: tt } = env.current;
      if (st.mode !== "live" || !st.threadId) return;
      const guest = st.plan.find((e) => e.guests?.length)?.guests?.[0];
      const body: Record<string, unknown> =
        kind === "behind" ? { type: "BEHIND_SCHEDULE", minutes_behind: 15 }
        : kind === "tired" ? { type: "GUEST_FATIGUE", guest_id: guest }
        : kind === "wait" ? { type: "WAIT_SPIKE", attraction_id: stop?.event_id, delta_minutes: 30 }
        : { type: "ATTRACTION_DOWN", attraction_id: stop?.event_id };
      const what =
        kind === "behind" ? tt("ev_what_behind")
        : kind === "tired" ? tt("ev_what_tired")
        : kind === "wait" ? tt("ev_what_wait", { title: stop?.title ?? "" })
        : tt("ev_what_down", { title: stop?.title ?? "" });
      try {
        const turn = await api.reportEvent(st.threadId, body);
        notify({ id: `ev:${Date.now()}`, icon: "📣", titleKey: "n_ev_t", bodyKey: "n_ev_b", vars: { what } }, false);
        if (turn.status === "done") {
          toast(tt("ev_none"));
          return;
        }
        toast(tt("ev_sent"));
        if (turn.status === "needs_event_confirmation") upd(() => ({ sheet: true }));
        await deliverTurn(turn);
      } catch (e) {
        toast(e instanceof ApiError ? (e.status === 422 ? tt("ev_unknown") : e.message) : String(e));
      }
    },
    [upd, toast, notify, deliverTurn],
  );

  const notifs = useMemo(() => notificationsOf(s, t), [s, t]);

  return {
    s,
    notifs,
    here,
    actions: {
      go, openSheet, closeSheet, openProposal, closeProposal, openNotifs, closeNotifs, openLang, closeLang,
      closeTop, closeAll, dismissSess, armSessionTimer, send, pickSuggestion, dismissSeed, confirmOk, confirmEdit,
      eventConfirm, provisionalPlan, applyProposal, keepPlan, markRead, dismissAlert, markAllRead, reviewProposal, setMap,
      zoomToMe, zoomBy, setMode, reset, toast, reportEvent,
    },
  };
}

export type Controller = ReturnType<typeof useController>;
type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never;
