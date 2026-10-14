import { useState, type FormEvent } from "react";
import { ApiError, auth } from "../api";
import { LANGS, type Lang, type TFn } from "../i18n";

interface Props {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: TFn;
  expired: boolean;
  onSignedIn: () => void;
}

export function Login({ lang, setLang, t, expired, onSignedIn }: Props) {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.login(user.trim(), pass);
      setPass("");
      onSignedIn();
    } catch (err) {
      setError(
        err instanceof ApiError ? (err.status === 429 ? t("login_locked") : t("login_bad")) : t("login_down"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="login-card" onSubmit={(e) => void submit(e)}>
        <h1>ParkMind</h1>
        <p className="login-sub">{t("login_title")}</p>
        {expired && !error && <p className="login-note" role="status">{t("login_expired")}</p>}
        {error && <p className="login-err" role="alert">{error}</p>}
        <label>
          {t("login_user")}
          <input
            name="username"
            autoComplete="username"
            autoFocus
            required
            maxLength={64}
            value={user}
            onChange={(e) => setUser(e.target.value)}
          />
        </label>
        <label>
          {t("login_pass")}
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={256}
            value={pass}
            onChange={(e) => setPass(e.target.value)}
          />
        </label>
        <button className="login-go" type="submit" disabled={busy || !user || !pass}>
          {t("login_go")}
        </button>
        <div className="login-langs">
          {LANGS.map((l) => (
            <button type="button" key={l} aria-pressed={l === lang} className={l === lang ? "on" : ""} onClick={() => setLang(l)}>
              {l.toUpperCase()}
            </button>
          ))}
        </div>
      </form>
    </main>
  );
}
