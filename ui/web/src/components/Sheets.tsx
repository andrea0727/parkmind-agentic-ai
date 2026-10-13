import { useEffect, useRef, useState, type ReactNode } from "react";
import { useApp } from "../ctx";
import { useDebug } from "../debug";
import { FIXTURE } from "../fixture";
import { WALK_M_PER_MIN, applyDiff, totalWalk } from "../geo";
import { I18N, LANGS } from "../i18n";
import type { ChatItem } from "../controller";
import type { ChipView } from "../types";

const SEED_ORDER = [1, 2, 10, 6, 5, 7, 8, 4, 9, 3];
const FOCUSABLE = "input,button,[tabindex]:not([tabindex='-1'])";

/* `inert` is not in React 18's typings; an empty attribute is what the browser needs. */
const inertProps = (inert: boolean) => (inert ? ({ inert: "" } as object) : {});

interface DialogProps {
  id: string;
  className: string;
  open: boolean;
  labelledBy: string;
  focus?: string;
  children: ReactNode;
}

function Dialog({ id, className, open, labelledBy, focus, children }: DialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (open && !wasOpen.current) {
      lastFocus.current = document.activeElement as HTMLElement | null;
      const timer = window.setTimeout(() => {
        const el = ref.current;
        if (!el) return;
        (el.querySelector<HTMLElement>(focus ?? FOCUSABLE) ?? el.querySelector<HTMLElement>(FOCUSABLE))?.focus();
      }, 320);
      wasOpen.current = true;
      return () => window.clearTimeout(timer);
    }
    if (!open && wasOpen.current) {
      wasOpen.current = false;
      const back = lastFocus.current;
      if (back && document.contains(back)) back.focus();
    }
    return undefined;
  }, [open, focus]);

  return (
    <div
      ref={ref}
      id={id}
      className={`${className}${open ? " on" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      {...inertProps(!open)}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------- Carousel */
function Carousel({ prompts, onPick }: { prompts: number[]; onPick: (title: string) => void }) {
  const { t } = useApp();
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const dragged = useRef(false);
  const [grabbing, setGrabbing] = useState(false);

  /* Touch scrolls natively; the manual drag is mouse-only and must not capture the pointer or the click never reaches the card. */
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = drag.current;
      const el = ref.current;
      if (!d || !el) return;
      const dx = e.clientX - d.x;
      if (!d.moved && Math.abs(dx) > 5) {
        d.moved = true;
        dragged.current = true;
      }
      if (d.moved) el.scrollLeft = d.left - dx;
    };
    const onUp = () => {
      if (!drag.current) return;
      drag.current = null;
      setGrabbing(false);
      window.setTimeout(() => {
        dragged.current = false;
      }, 0);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, []);

  return (
    <div className="carousel">
      <div
        ref={ref}
        className={`suggest${grabbing ? " grabbing" : ""}`}
        onPointerDown={(e) => {
          if (e.pointerType !== "mouse") return;
          drag.current = { x: e.clientX, left: ref.current?.scrollLeft ?? 0, moved: false };
          dragged.current = false;
          setGrabbing(true);
        }}
      >
        {prompts.map((i) => (
          <button
            key={i}
            type="button"
            className="scard"
            onClick={() => {
              if (dragged.current) return;
              onPick(t(`prompt${i}`));
            }}
          >
            <b>{t(`prompt${i}`)}</b>
            <span>{t(`prompt${i}s`)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- Chat items */
function Bubble({ children }: { children: ReactNode }) {
  return (
    <div className="bot-wrap">
      <div className="bot-av">✦</div>
      <div className="bot">{children}</div>
    </div>
  );
}

function Tag() {
  return <div className="tag">PARK<i>MIND</i></div>;
}

function Chips({ chips }: { chips?: ChipView[] }) {
  if (!chips?.length) return null;
  return (
    <div className="constraints">
      {chips.map((c, i) => (
        <span key={i} className={`cst ${c.kind}`}>{c.label}</span>
      ))}
    </div>
  );
}

function ConfirmCard({ item }: { item: Extract<ChatItem, { k: "confirm" }> }) {
  const { c, t } = useApp();
  const [consent, setConsent] = useState(false);
  const [warn, setWarn] = useState("");
  return (
    <div className="confirm">
      <b>{t("cf_title")}</b>
      <p>{t("cf_sub")}</p>
      <ul className="cf-list">
        {item.chips.map((ch, i) => (
          <li key={i}><span>{ch.label}</span><i>{t("cf_hard")}</i></li>
        ))}
      </ul>
      <label className="cf-consent">
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => {
            setConsent(e.target.checked);
            if (e.target.checked) setWarn("");
          }}
        />
        <span><b>{t("cf_consent")}</b>{t("cf_consent_b")}</span>
      </label>
      <div className="cf-actions">
        <button type="button" className="cf-edit" onClick={() => c.actions.confirmEdit(item.id)}>{t("cf_edit")}</button>
        <button
          type="button"
          className="cf-ok"
          onClick={() => {
            if (item.consentRequired && !consent) {
              setWarn(t("cf_need"));
              return;
            }
            void c.actions.confirmOk(item.id, consent);
          }}
        >
          {t("cf_ok")}
        </button>
      </div>
      <p className="cf-warn" role="status" aria-live="polite">{warn}</p>
    </div>
  );
}

function PlanPreview() {
  const { c, t } = useApp();
  const { plan } = c.s;
  return (
    <div className="propcard">
      <b>{plan.length} {t("stops_exit").split("·")[0].trim()}</b>
      {plan.slice(0, 3).map((e) => (
        <div className="propline" key={e.event_id}><span className="t">{e.start}</span><span>{e.title}</span></div>
      ))}
      <button
        type="button"
        className="go"
        onClick={() => {
          c.actions.closeSheet();
          c.actions.go("plan");
        }}
      >
        {t("my_plan")}
      </button>
    </div>
  );
}

function ChatRow({ item }: { item: ChatItem }) {
  const { c, t } = useApp();
  switch (item.k) {
    case "me":
      return <div className="msg me">{item.text}</div>;
    case "meta":
      return <div className="meta">{item.text}</div>;
    case "typing":
      return (
        <Bubble>
          <div className="typing" role="status" aria-label={t("thinking")}><i /><i /><i /></div>
        </Bubble>
      );
    case "bot":
      return (
        <Bubble>
          <Tag />
          {item.text && <p>{item.text}</p>}
          <Chips chips={item.chips} />
          {item.preview && <PlanPreview />}
        </Bubble>
      );
    case "suggest":
      return (
        <>
          <div className="suggest-label"><span>✨</span> {t(item.label)}</div>
          <Carousel prompts={item.ids} onPick={(title) => c.actions.pickSuggestion(item.id, title)} />
        </>
      );
    case "confirm":
      return <ConfirmCard item={item} />;
    case "evconfirm":
      return (
        <Bubble>
          <Tag />
          <p>{item.text}</p>
          <div className="cf-actions">
            <button type="button" className="cf-edit" onClick={() => void c.actions.eventConfirm(item.id, false)}>{t("ev_no")}</button>
            <button type="button" className="cf-ok" onClick={() => void c.actions.eventConfirm(item.id, true)}>{t("ev_yes")}</button>
          </div>
        </Bubble>
      );
  }
}

/* ------------------------------------------------------------ Assistant */
export function AssistantSheet() {
  const { c, t } = useApp();
  const { s } = c;
  const a = c.actions;
  const chatRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const el = chatRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [s.chat, s.seeded, s.sheet]);

  const h = new Date().getHours();
  const greet = h < 12 ? t("hi_morning") : h < 19 ? t("hi_afternoon") : t("hi_evening");

  const submit = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft("");
    void a.send(text);
  };

  return (
    <Dialog id="sheet" className="sheet" open={s.sheet} labelledBy="sheetTitle" focus="#chatInput">
      <div className="grab" />
      <div className="sheet-head">
        <div>
          <h3 id="sheetTitle">{t("assistant")}</h3>
          <div className="sub" id="assistStatus">{s.thinking ? t("thinking") : t("listening")}</div>
        </div>
        <button className="xbtn" aria-label={t("a_close")} onClick={a.closeSheet}>✕</button>
      </div>
      <div className="chat" id="chat" ref={chatRef}>
        {s.seeded && (
          <>
            <div className="welcome">
              <div className="em">💬</div>
              <h4>{s.mode === "live" ? greet : `${greet} ${FIXTURE.guest_profile.display_name}`}</h4>
              <p>{t("welcome_desc")}</p>
            </div>
            {s.mode !== "live" && (
              <>
                <div className="suggest-label"><span>✨</span> {t("try_topics")}</div>
                <Carousel prompts={SEED_ORDER} onPick={(title) => void a.send(title)} />
              </>
            )}
          </>
        )}
        {s.chat.map((item) => (
          <ChatRow key={item.id} item={item} />
        ))}
      </div>
      <div className="composer">
        <input
          id="chatInput"
          autoComplete="off"
          aria-label={t("composer")}
          placeholder={t("composer")}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            a.armSessionTimer();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button className="send" aria-label={t("a_send")} onClick={submit}>↑</button>
      </div>
    </Dialog>
  );
}

/* ----------------------------------------------------------------- Lang */
export function LangSheet() {
  const { c, t, lang, setLang } = useApp();
  return (
    <Dialog id="langsheet" className="langsheet" open={c.s.lsheet} labelledBy="lTitle">
      <div className="grab" />
      <h3 id="lTitle">{t("language")}</h3>
      <p>{t("lang_note")}</p>
      <div className="langgrid">
        {LANGS.map((code) => (
          <button
            key={code}
            type="button"
            className={`lang${code === lang ? " on" : ""}`}
            aria-pressed={code === lang}
            onClick={() => {
              setLang(code);
              c.actions.closeLang();
              c.actions.toast(I18N[code].name);
            }}
          >
            <span className="fl">{I18N[code].flag}</span>
            <span>{I18N[code].name}</span>
          </button>
        ))}
      </div>
    </Dialog>
  );
}

/* --------------------------------------------------------------- Notifs */
export function NotifSheet() {
  const { c, t } = useApp();
  const { s, notifs } = c;
  const a = c.actions;
  return (
    <Dialog id="nsheet" className="nsheet" open={s.nsheet} labelledBy="nTitle">
      <div className="grab" />
      <div className="sheet-head">
        <h3 id="nTitle">{t("notifs")}</h3>
        <button className="xbtn" aria-label={t("a_close")} onClick={a.closeNotifs}>✕</button>
      </div>
      <div className="nbody">
        {notifs.length === 0 && <p className="nempty">{t("notifs_empty")}</p>}
        {notifs.map((n) => (
          <div
            key={n.id}
            className={`notif ${n.kind}${s.read.includes(n.id) ? " read" : ""}`}
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("button")) return;
              a.markRead(n.id);
            }}
          >
            <div className="ic">{n.icon}</div>
            <div style={{ flex: 1 }}>
              <b>{n.title}</b>
              <p>{n.body}</p>
              <span className="when">{n.mins ? t("n_min", { n: n.mins }) : t("n_now")}</span>
            </div>
            {n.action === "review" ? (
              <button className="act" onClick={a.reviewProposal}>{t("review")}</button>
            ) : (
              <button className="dis" aria-label={t("a_dismiss")} onClick={() => a.dismissAlert(n.id)}>✕</button>
            )}
          </div>
        ))}
      </div>
      <div className="nfoot">
        <button onClick={a.markAllRead}>{t("notifs_clear")}</button>
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------- Proposal */
const signed = (n: number) => `${n > 0 ? "+" : ""}${n}`;

export function ProposalSheet() {
  const { c, t, loc } = useApp();
  const { s } = c;
  const a = c.actions;
  const p = s.proposal;
  const { enabled: debugOn } = useDebug();

  let stats: ReactNode = null;
  if (p) {
    const m = p.metrics;
    if (s.mode === "live") {
      const cell = (v: number | null | undefined, label: string, absolute: boolean) => (
        <div className="stat">
          <b>{v == null ? "—" : absolute ? Math.round(v) : signed(Math.round(v))}</b>
          <small>{label}</small>
        </div>
      );
      const initial = !!m.initial;
      stats = (
        <>
          {cell(initial ? m.walk_total_min : m.walk_delta_min, loc(m.walk_label), initial)}
          {cell(initial ? m.wait_total_min : m.wait_delta_min, t("stat_wait"), initial)}
        </>
      );
    } else {
      const before = totalWalk(s.plan);
      const after = totalWalk(applyDiff(s.plan, p));
      const dMin = Math.round((after - before) / WALK_M_PER_MIN);
      const touched = p.diff.removed.length + p.diff.moved.length;
      const total = s.plan.length;
      stats = (
        <>
          <div className="stat"><b>{Math.max(total - touched, 0)}/{total}</b><small>{loc(m.unchanged_label)}</small></div>
          <div className="stat"><b>{signed(dMin)}</b><small>{loc(m.walk_label)}</small></div>
          <div className="stat"><b>{m.affinity}</b><small>{loc(m.affinity_label)}</small></div>
        </>
      );
    }
  }

  return (
    <Dialog id="psheet" className="psheet" open={s.psheet} labelledBy="pTitle" focus=".btn.apply">
      <div className="grab" />
      <div className="pbody">
        <h3 id="pTitle">{t("proposed")}</h3>
        {p && (
          <>
            <p className="why">{loc(p.explanation).startsWith(loc(p.trigger)) ? loc(p.explanation) : `${loc(p.trigger)} ${loc(p.explanation)}`}</p>
            <div className="diff">
              {p.diff.removed.map((x) => (
                <div className="drow rm" key={`r${x.ref}`}>
                  <div className="s">−</div>
                  <div><div className="b">{x.start} · {x.title}</div><div className="m">{loc(x.meta)}</div></div>
                </div>
              ))}
              {p.diff.added.map((x) => (
                <div className="drow add" key={`a${x.ref}`}>
                  <div className="s">+</div>
                  <div><div className="b">{x.start} · {x.title}</div><div className="m">{loc(x.meta)}</div></div>
                </div>
              ))}
              {p.diff.moved.map((x) => (
                <div className="drow mv" key={`m${x.ref}`}>
                  <div className="s">↻</div>
                  <div><div className="b">{x.from} → {x.to} · {x.title}</div><div className="m">{loc(x.meta)}</div></div>
                </div>
              ))}
            </div>
            <div className="stats">{stats}</div>
            <div className="verified">
              <b>{t("a11y_ok")}</b>
              <p>{s.mode === "live" ? t("a11y_live") : loc(p.accessibility_note)}</p>
            </div>
            {debugOn && (
              <p className="note devprov">
                <b>{t("dev_prov")}</b><br />
                {loc(p.provenance)}
              </p>
            )}
          </>
        )}
      </div>
      <div className="pfoot">
        <div className="pactions">
          <button className="btn keep" onClick={() => void a.keepPlan()}>{t("keep")}</button>
          <button className="btn apply" onClick={() => void a.applyProposal()}>{t("apply")}</button>
        </div>
        <p className="fine">{t("nothing_until")}</p>
      </div>
    </Dialog>
  );
}

/* --------------------------------------------------------- Sess + toast */
export function SessBar() {
  const { c, t } = useApp();
  const on = c.s.sessbar;
  return (
    <div className={`sessbar${on ? " on" : ""}`} id="sessbar" role="status" aria-live="polite" {...inertProps(!on)}>
      <div className="ic">✦</div>
      <div style={{ flex: 1 }}><b>{t("sess_title")}</b><small>{t("sess_sub")}</small></div>
      <button onClick={c.actions.dismissSess}>{t("dismiss")}</button>
    </div>
  );
}

export function Toast() {
  const { c } = useApp();
  return (
    <div className={`toast${c.s.toast ? " on" : ""}`} id="toast" role="status" aria-live="polite">
      {c.s.toast}
    </div>
  );
}
