import { useEffect, useRef, useState, type ReactNode } from "react";
import { useApp } from "../ctx";
import { debug, useDebug, type ApiCall } from "../debug";
import { FIXTURE } from "../fixture";
import { haversine, legMeters, totalWalk, walkMin } from "../geo";
import type { EventKind, Screen as ScreenName } from "../controller";
import type { PlanEvent } from "../types";
import { BigMap, PlanMap, visibleRides } from "./ParkMap";

function Pane({ id, children }: { id: ScreenName; children: ReactNode }) {
  const { c } = useApp();
  const on = c.s.screen === id;
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (on && ref.current) ref.current.scrollTop = 0;
  }, [on]);
  return (
    <section ref={ref} className={`screen${on ? " on" : ""}`} id={`s-${id}`}>
      {children}
    </section>
  );
}

function LiveNote() {
  const { c, t } = useApp();
  if (c.s.mode !== "live") return null;
  return <p className="note live-note">{t("live_static")}</p>;
}

function greetWord(t: (k: string) => string) {
  const h = new Date().getHours();
  return h < 12 ? t("hi_morning") : h < 19 ? t("hi_afternoon") : t("hi_evening");
}

/* ---------------------------------------------------------------- Home */
export function HomeScreen() {
  const { c, t, loc, lang } = useApp();
  const { s, notifs } = c;
  const a = c.actions;
  const first = s.plan.find((e) => e.state === "current") ?? s.plan[0];
  const unread = notifs.filter((n) => !s.read.includes(n.id)).length;
  const live = s.mode === "live";

  return (
    <Pane id="home">
      <div className="brandbar">
        <div className="wordmark">park<i>mind</i></div>
        <div className="brand-actions">
          <button className="langbtn" aria-label={t("a_lang")} onClick={a.openLang}>
            <span>{lang.toUpperCase()}</span>
          </button>
          <button className={`bell${unread > 0 ? " has" : ""}`} aria-label={t("a_notifs")} onClick={a.openNotifs}>
            <span className="g">🔔</span>
            <span className={`badge${unread === 0 ? " hide" : ""}`}>{unread > 9 ? "9+" : unread}</span>
          </button>
          <button className="avatar" aria-label={t("a_profile")} onClick={() => a.go("profile")}>{live ? "👤" : "A"}</button>
          <button
            className={`spark-btn${s.proposalState === "pending" ? " ping" : ""}`}
            aria-label={t("a_assistant")}
            onClick={a.openSheet}
          >
            <span>✦</span>
          </button>
        </div>
      </div>
      {live ? (
        <>
          <h2 className="greet">{greetWord(t)}</h2>
          <p className="subline">{FIXTURE.plan.park}</p>
        </>
      ) : (
        <>
          <h2 className="greet">{greetWord(t)},<br />{FIXTURE.guest_profile.display_name}</h2>
          <p className="subline">{FIXTURE.plan.park} · {t("party")} {FIXTURE.guest_profile.party_size}</p>
          <div className="pills">
            <span className="pill">☀️ 29°</span>
            <span className="pill warn">{t("rain")}</span>
            <span className="pill live">{t("busy")}</span>
          </div>
        </>
      )}

      <h3 className="sect">{t("your_day")}</h3>
      <div className="tiles">
        <button className="tile plan" onClick={() => a.go("plan")}>
          <div className="ico">🗺️</div>
          <div><span className="big">{s.plan.length}</span><b>{t("my_plan")}</b><small>{t("stops_exit")}</small></div>
        </button>
        <button className="tile waits" onClick={() => a.go("park")}>
          <div className="ico">⏱️</div>
          <div><b>{t("wait_times")}</b><small>{t("live_rides")}</small></div>
        </button>
      </div>

      <div className="panel">
        <div className="panel-head"><b>{t("now")}</b><span>{t("updated")}</span></div>
        {first ? (
          <div className="nowrow">
            <div className="t">{first.start}</div>
            <div className="rail"><div className="dot" /><div className="line" /></div>
            <div>
              <div className="b">{first.title}</div>
              <div className="m">{loc(first.meta)}</div>
              <div className="a11y">{t("no_stairs")}</div>
            </div>
          </div>
        ) : (
          <div className="empty">
            <b>{t("no_plan")}</b>
            <p>{t("no_plan_sub")}</p>
          </div>
        )}
      </div>

      {s.proposalState === "pending" && (
        <div className="alert">
          <div className="ic">🌧️</div>
          <div><b>{t("change_title")}</b><p>{live && s.proposal ? loc(s.proposal.trigger) : t("change_body")}</p></div>
          <button className="cta" onClick={a.openProposal}>{t("review")}</button>
        </div>
      )}
      {s.proposalState === "kept" && (
        <div className="alert kept">
          <div className="ic">🌧️</div>
          <div><b>{t("kept_title")}</b><p>{t("kept_body")}</p></div>
          <button className="cta" onClick={a.openProposal}>{t("review")}</button>
        </div>
      )}
    </Pane>
  );
}

/* ---------------------------------------------------------------- Plan */
export function PlanScreen() {
  const { c, t, loc } = useApp();
  const { s } = c;
  const legs = totalWalk(s.plan);

  return (
    <Pane id="plan">
      <div className="screen-head">
        <h2>{t("my_plan")}</h2>
        <p>{FIXTURE.plan.park} · <span>{s.plan.length}</span> <span>{t("stops_exit")}</span></p>
        {s.plan.length > 0 && (
          <span className="status-chip">{s.proposalState === "applied" ? t("plan_updated") : t("plan_valid")}</span>
        )}
      </div>
      {s.plan.length === 0 ? (
        <div className="empty">
          <b>{t("no_plan")}</b>
          <p>{t("no_plan_sub")}</p>
          <button className="rc-add" onClick={c.actions.openSheet}>{t("a_assistant")}</button>
        </div>
      ) : (
        <>
          <PlanMap plan={s.plan} label={t("a_route")} />
          <p className="walktotal">{t("walk_total", { km: (legs / 1000).toFixed(2), min: walkMin(legs) ?? 0 })}</p>
          <div>
            {s.plan.map((ev, i) => {
              const dist = legMeters(s.plan[i - 1], ev);
              const cls = ["stop", ev.state === "current" ? "current" : ev.state === "done" ? "done" : "", ev.changed ? "changed" : ""]
                .filter(Boolean)
                .join(" ");
              return (
                <div className={cls} key={ev.event_id}>
                  <div className="t">{ev.start}</div>
                  <div className="rail"><div className="dot" /><div className="line" /></div>
                  <div className="body">
                    {dist != null && <div className="leg">↓ {t("walk_leg", { m: dist, min: walkMin(dist) ?? 0 })}</div>}
                    {ev.changed && <span className="tagnew">{t(ev.changed)}</span>}
                    {ev.lightning_lane && <span className="lltag">⚡ {t("ll_tag")}</span>}
                    <div className="b">{ev.title}</div>
                    <div className="m">{loc(ev.meta)}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
      {s.mode === "live" && s.plan.length > 0 && <ReportChange />}
      <p className="note">{t("plan_note")}</p>
    </Pane>
  );
}

function ReportChange() {
  const { c, t } = useApp();
  const [pick, setPick] = useState<"wait" | "down" | null>(null);
  const rides = c.s.plan.filter((e) => e.kind === "ATTRACTION");
  const send = (kind: EventKind, stop?: PlanEvent) => {
    setPick(null);
    void c.actions.reportEvent(kind, stop);
  };
  return (
    <section className="report" aria-label={t("ev_title")}>
      <b>{t("ev_title")}</b>
      <div className="rp-chips">
        <button type="button" onClick={() => send("behind")}>{t("ev_behind")}</button>
        <button type="button" onClick={() => send("tired")}>{t("ev_tired")}</button>
        <button type="button" aria-pressed={pick === "wait"} onClick={() => setPick(pick === "wait" ? null : "wait")}>{t("ev_wait")}</button>
        <button type="button" aria-pressed={pick === "down"} onClick={() => setPick(pick === "down" ? null : "down")}>{t("ev_down")}</button>
      </div>
      {pick && (
        <div className="rp-pick">
          <small>{t("ev_pick")}</small>
          {rides.map((r) => (
            <button type="button" key={r.event_id} onClick={() => send(pick, r)}>{r.title}</button>
          ))}
        </div>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- Park */
function RideCard() {
  const { c, t } = useApp();
  const { s } = c;
  const r = FIXTURE.rides.find((x) => x.name === s.map.selected);
  if (!r) return <div className="ridecard"><p className="hint">{t("tap_hint")}</p></div>;
  const d = haversine(c.here(), r.loc);
  const inPlan = s.plan.some((e) => e.title === r.name);
  const ask = () => {
    c.actions.openSheet();
    void c.actions.send(`${t("ask_add")}: ${r.name}`);
  };
  return (
    <div className="ridecard">
      <div className="rc-head">
        <div><b>{r.name}</b><small>{r.zone}</small></div>
        <button className="xsmall" id="rcClose" aria-label={t("a_close")} onClick={() => c.actions.setMap({ selected: null })}>✕</button>
      </div>
      <div className="rc-stats">
        <span className="rc-stat">{r.closed ? t("closed_now") : `${r.wait} ${t("min_wait")}`}</span>
        <span className="rc-stat">{d} m · {walkMin(d)} min</span>
        {inPlan && <span className="rc-stat on">{t("in_plan")}</span>}
      </div>
      {r.access ? <div className="a11y">{t("wc_yes")}</div> : <div className="a11y no">{t("wc_no")}</div>}
      {!inPlan && r.access && !r.closed && (
        <button className="rc-add" id="rcAdd" onClick={ask}>{t("ask_add")}</button>
      )}
    </div>
  );
}

const FILTERS: { id: "all" | "plan" | "access" | "open"; key: string }[] = [
  { id: "all", key: "f_all" },
  { id: "plan", key: "f_route" },
  { id: "access", key: "f_access" },
  { id: "open", key: "f_open" },
];

export function ParkScreen() {
  const { c, t } = useApp();
  const { s } = c;
  const a = c.actions;

  return (
    <Pane id="park">
      <div className="screen-head">
        <h2>{t("park")}</h2>
        <p>{t("park_sub")}</p>
      </div>
      <LiveNote />
      <div className="mapfilters">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={`mf${s.map.filter === f.id ? " on" : ""}`}
            aria-pressed={s.map.filter === f.id}
            onClick={() => a.setMap({ filter: f.id, selected: null })}
          >
            {t(f.key)}
          </button>
        ))}
      </div>
      <div className="mapwrap">
        <BigMap />
        <div className="mapzoom">
          <button aria-label={t("a_in")} onClick={() => a.zoomBy(1.4)}>+</button>
          <button aria-label={t("a_out")} onClick={() => a.zoomBy(0.72)}>−</button>
          <button aria-label={t("a_me")} onClick={a.zoomToMe}>◎</button>
          <button aria-label={t("a_all")} onClick={() => a.setMap({ zoom: 1, cx: 0.5, cy: 0.5 })}>⤢</button>
        </div>
        <div className="maplegend">
          <span><i style={{ background: "#4A2FA8" }} /><span>{t("l_route")}</span></span>
          <span><i style={{ background: "#12938C" }} /><span>{t("l_short")}</span></span>
          <span><i style={{ background: "#FFB627" }} /><span>{t("l_long")}</span></span>
          <span><i style={{ background: "#E0218A" }} /><span>{t("l_closed")}</span></span>
        </div>
      </div>
      <RideCard />
      <h3 className="sect" style={{ marginTop: 20 }}>{t("all_rides")}</h3>
      <div>
        {visibleRides(s.plan, s.map.filter).map((r) => {
          let cls = "wait low";
          let txt: ReactNode = <>{r.wait}<small>min</small></>;
          if (r.closed) { cls = "wait closed"; txt = t("closed_now"); }
          else if ((r.wait ?? 0) >= 60) cls = "wait high";
          else if ((r.wait ?? 0) >= 20) cls = "wait mid";
          const d = haversine(c.here(), r.loc);
          return (
            <button
              key={r.name}
              type="button"
              className="ride"
              style={r.access ? undefined : { opacity: 0.5 }}
              aria-label={`${r.name}, ${r.zone}, ${r.closed ? t("closed_now") : `${r.wait} ${t("min_wait")}`}, ${d} m`}
              onClick={() => {
                a.setMap({ selected: r.name });
                document.getElementById("s-park")?.scrollTo({ top: 0, behavior: "smooth" });
              }}
            >
              <div>
                <b>{r.name}</b>
                <small>{r.zone}{d != null ? ` · ${d} m` : ""}{r.access ? "" : ` · ${t("wc_no").replace("♿ ", "")}`}</small>
              </div>
              <div className={cls}>{txt}</div>
            </button>
          );
        })}
      </div>
    </Pane>
  );
}

/* ------------------------------------------------------------- Profile */
function Ticket() {
  const { t, lang } = useApp();
  const k = FIXTURE.tickets;
  const ll = k.lightning_lane;
  const left = ll.selections_total - ll.selections_used;
  const fmt = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });
  return (
    <div className="ticket">
      <div className="tk-top">
        <div><b>{t(k.product_key)}</b><small>{t("tk_valid", { a: fmt(k.valid_from), b: fmt(k.valid_to) })}</small></div>
        <span className="tk-day">{t("tk_day", { i: k.day_index, n: k.day_total })}</span>
      </div>
      <div className="tk-perf">
        <span className={`tag ${k.park_hopper ? "hard" : "plain"}`}>{k.park_hopper ? t("tk_hopper_yes") : t("tk_hopper_no")}</span>
      </div>
      <div className="tk-ll">
        <div><b>⚡ {t("tk_ll")}</b><small>{t("tk_ll_left", { n: left, t: ll.selections_total })}</small></div>
        <div className="tk-dots">
          {Array.from({ length: ll.selections_total }, (_, i) => (
            <i key={i} className={i < ll.selections_used ? "used" : ""} />
          ))}
        </div>
      </div>
      <div className="tk-holders">
        {k.holders.map((h) => (
          <span className="tag plain" key={h.name}>{h.name} · {h.kind === "child" ? t("tk_child") : t("tk_adult")}</span>
        ))}
      </div>
    </div>
  );
}

export function ProfileScreen() {
  const { c, t, loc } = useApp();
  return (
    <Pane id="profile">
      <div className="screen-head">
        <button className="backbtn" aria-label={t("a_back")} onClick={() => c.actions.go("home")}>‹</button>
        <h2>{t("profile")}</h2>
        <p>{t("profile_sub")}</p>
      </div>
      <LiveNote />
      <div className="prof-head">
        <div className="prof-av">A</div>
        <div><b>Andrea</b><small>{t("party")} · {FIXTURE.guest_profile.party_size}</small></div>
      </div>

      <h3 className="sect" style={{ marginTop: 4 }}>{t("tickets")}</h3>
      <p className="scopetag">{t("tk_future")}</p>
      <Ticket />
      <p className="note">{t("tk_note")}</p>

      <h3 className="sect" style={{ marginTop: 20 }}>{t("party")}</h3>
      <div>
        {FIXTURE.party.map((m) => (
          <div className="row" key={m.name}><span>{m.name}<small>{loc(m.note)}</small></span></div>
        ))}
      </div>

      <h3 className="sect" style={{ marginTop: 20 }}>{t("access_req")}</h3>
      <div className="tagrow">
        {FIXTURE.accessibility_requirements.flags.map((f) => (
          <span className="tag hard" key={f.code}>♿ {loc(f.label)}</span>
        ))}
        <span className="tag plain">{t("stops_exit").split("·").pop()?.trim()}</span>
      </div>
      <p className="note">{t("a11y_note")}</p>

      <h3 className="sect" style={{ marginTop: 20 }}>{t("prefs")}</h3>
      <div className="tagrow">
        {FIXTURE.guest_preferences.soft.map((x) => (
          <span className="tag soft" key={x.code}>{loc(x.label)}</span>
        ))}
      </div>
    </Pane>
  );
}

/* ------------------------------------------------------------- Dev tools */
const pretty = (v: unknown) => JSON.stringify(v, null, 2);

function CallRow({ call }: { call: ApiCall }) {
  const { t } = useApp();
  const ok = call.status !== null && call.status < 400;
  return (
    <details className="devcall">
      <summary>
        <span className="m">{call.method}</span>
        <span className="p">{call.path}</span>
        <span className={`st ${ok ? "ok" : "err"}`}>{call.status ?? "ERR"}</span>
        <span className="ms">{call.ms} ms</span>
      </summary>
      {call.request !== undefined && (
        <>
          <h5>{t("dev_req")}</h5>
          <pre>{pretty(call.request)}</pre>
        </>
      )}
      <h5>{t("dev_res")}</h5>
      <pre>{call.response === undefined ? t("dev_nobody") : pretty(call.response)}</pre>
    </details>
  );
}

function DevTools() {
  const { t } = useApp();
  const { enabled, calls } = useDebug();
  return (
    <>
      <button className="row" onClick={() => debug.setEnabled(!enabled)} aria-pressed={enabled}>
        <span>{t("dev_row")}<small>{t("dev_sub")} · {enabled ? t("dev_on") : t("dev_off")}</small></span>
        <span className="chev">{enabled ? "●" : "○"}</span>
      </button>
      {enabled && (
        <>
          <button className="row" onClick={debug.clear}><span>{t("dev_clear")}</span><span className="chev">⌫</span></button>
          <div className="devlog">
            {calls.length === 0 && <p className="note">{t("dev_empty")}</p>}
            {calls.map((call) => (
              <CallRow key={call.id} call={call} />
            ))}
          </div>
        </>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- More */
export function MoreScreen() {
  const { c, t } = useApp();
  const { s } = c;
  return (
    <Pane id="more">
      <div className="screen-head">
        <h2>{t("more")}</h2>
        <p>{t("more_sub")}</p>
      </div>
      <button className="row" onClick={() => c.actions.go("profile")}><span>{t("profile")}</span><span className="chev">›</span></button>
      <button className="row" onClick={() => c.actions.go("eval")}>
        <span>{t("eval_row")}<small>ParkMind vs. baseline LLM-only</small></span><span className="chev">›</span>
      </button>
      <button className="row" onClick={() => void c.actions.reset()}>
        <span>{t("reset_row")}<small>{t("reset_row_sub")}</small></span><span className="chev">↺</span>
      </button>
      <DevTools />
      <p className="note" style={{ marginTop: 22 }}>{t("data_src")}</p>
      <div className="switch" style={{ marginTop: 8 }}>
        <button className={s.mode === "mock" ? "on" : ""} onClick={() => void c.actions.setMode("mock")}>Mock (fixture)</button>
        <button className={s.mode === "live" ? "on" : ""} onClick={() => void c.actions.setMode("live")}>Live (API)</button>
      </div>
      <p className="note">{t("mode_note")}</p>
    </Pane>
  );
}

/* ---------------------------------------------------------------- Eval */
export function EvalScreen() {
  const { c, t } = useApp();
  return (
    <Pane id="eval">
      <div className="screen-head">
        <button className="backbtn" aria-label={t("a_back")} onClick={() => c.actions.go("more")}>‹</button>
        <h2>{t("eval")}</h2>
        <p>{t("eval_sub")}</p>
      </div>
      <LiveNote />
      <div className="metric"><div className="n">Preference Alignment Score<small>{t("m1")}</small></div><div className="v">0.87</div><div className="base">0.61</div></div>
      <div className="metric"><div className="n">Personalization Lift<small>{t("m2")}</small></div><div className="v">+0.34</div><div className="base">—</div></div>
      <div className="metric"><div className="n">{t("m3")}</div><div className="v">0</div><div className="base">7</div></div>
      <div className="metric"><div className="n">{t("m4")}</div><div className="v">91%</div><div className="base">—</div></div>
      <p className="note">{t("eval_note")}</p>
    </Pane>
  );
}
