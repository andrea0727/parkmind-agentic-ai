import { useEffect, useMemo, useRef, useState } from "react";
import { AppCtx, type Ctx } from "./ctx";
import { useController } from "./controller";
import { makeLoc, makeT, readLang, saveLang, type Lang } from "./i18n";
import { AssistantSheet, LangSheet, NotifSheet, ProposalSheet, SessBar, Toast } from "./components/Sheets";
import { EvalScreen, HomeScreen, MoreScreen, ParkScreen, PlanScreen, ProfileScreen } from "./components/Screens";

const clock = () => {
  const d = new Date();
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
};

const TABS = [
  { id: "home", glyph: "⌂", key: "t_home" },
  { id: "park", glyph: "▲", key: "t_park" },
  { id: "plan", glyph: "◷", key: "t_plan" },
  { id: "assistant", glyph: "✦", key: "t_ai" },
  { id: "more", glyph: "☰", key: "t_more" },
] as const;

function Shell({ lang, setLang }: { lang: Lang; setLang: (l: Lang) => void }) {
  const t = useMemo(() => makeT(lang), [lang]);
  const loc = useMemo(() => makeLoc(lang), [lang]);
  const c = useController(lang, t, loc);
  const ctx = useMemo<Ctx>(() => ({ lang, setLang, t, loc, c }), [lang, setLang, t, loc, c]);
  const { s, actions: a } = c;
  const [time, setTime] = useState(clock);
  const deviceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = window.setInterval(() => setTime(clock()), 20000);
    return () => window.clearInterval(id);
  }, []);

  const overlay = s.psheet || s.lsheet || s.nsheet || s.sheet;
  const closeTop = a.closeTop;

  /* Escape closes the topmost dialog; Tab is trapped inside it. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!overlay) return;
      if (e.key === "Escape") {
        closeTop();
        return;
      }
      if (e.key !== "Tab") return;
      const open = deviceRef.current?.querySelector<HTMLElement>(
        s.psheet ? "#psheet" : s.lsheet ? "#langsheet" : s.nsheet ? "#nsheet" : "#sheet",
      );
      if (!open) return;
      const items = [...open.querySelectorAll<HTMLElement>("input,button,[tabindex]:not([tabindex='-1'])")].filter(
        (el) => !el.hasAttribute("disabled") && el.offsetParent !== null,
      );
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!open.contains(active)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [overlay, closeTop, s.psheet, s.lsheet, s.nsheet]);

  const tabOn = (id: string) =>
    id === "assistant" ? s.sheet : !s.sheet && (id === "more" ? s.screen === "more" || s.screen === "profile" || s.screen === "eval" : s.screen === id);

  return (
    <AppCtx.Provider value={ctx}>
      <div className="stage-head">
        <h1>ParkMind · interactive prototype</h1>
        <p>Aurora Park · fictional park</p>
        {s.mode === "mock" && !s.booting && <span className="demo-badge">{t("demo")}</span>}
      </div>

      <div className="device" ref={deviceRef}>
        <div className="notch" />
        <div className="statusbar"><span>{time}</span><span className="sig">▂▄▆ ᯤ ▮</span></div>

        <main className="viewport">
          <HomeScreen />
          <ParkScreen />
          <PlanScreen />
          <MoreScreen />
          <ProfileScreen />
          <EvalScreen />
        </main>

        <div className={`scrim${overlay ? " on" : ""}`} onClick={a.closeTop} />

        <AssistantSheet />
        <LangSheet />
        <NotifSheet />
        <SessBar />
        <ProposalSheet />
        <Toast />

        <nav className="tabs" aria-label={t("a_nav")}>
          {TABS.map((tb) => (
            <button
              key={tb.id}
              className={`tab${tabOn(tb.id) ? " on" : ""}`}
              aria-current={tabOn(tb.id) ? "page" : undefined}
              onClick={() => a.go(tb.id)}
            >
              <span className="g">{tb.glyph}</span>
              <span>{t(tb.key)}</span>
            </button>
          ))}
        </nav>
      </div>

      <div className="stage-foot">
        <span style={{ fontSize: 12, opacity: 0.6, fontWeight: 500 }}>
          Interface mockup · illustrative data · not a production implementation
        </span>
        <button className="ghost-btn" onClick={() => void a.reset()}>Reset</button>
      </div>
    </AppCtx.Provider>
  );
}

export default function App() {
  const [lang, setLangState] = useState<Lang>(readLang);
  const setLang = (l: Lang) => {
    setLangState(l);
    saveLang(l);
  };
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  return <Shell lang={lang} setLang={setLang} />;
}
