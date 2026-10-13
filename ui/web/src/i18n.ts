import { I18N, type Dict, type Lang } from "./i18n.data";
import type { Loc } from "./types";

export { I18N };
export type { Lang };

export const LANGS = Object.keys(I18N) as Lang[];

/** Strings the prototype never needed because it had no live mode. */
export const EXTRA: Record<Lang, Dict> = {
  es: {
    a_map: "Mapa del parque",
    a_route: "Ruta del día",
    live_fail: "Sin conexión con /api — volviendo a modo Mock.",
    live_on: "Conectado a la API · sesión nueva",
    no_plan: "Aún no hay plan",
    no_plan_sub: "Cuéntale al asistente sobre tu grupo y armará tu día.",
    live_static: "Esta pantalla usa datos ilustrativos: la API aún no los expone.",
    stat_wait: "min de espera",
    a11y_live: "Cada parada pasó por la puerta de restricciones antes de proponerse. Accesibilidad falla cerrado.",
    moved_meta: "movida en el horario",
    ev_confirm: "¿Confirmas este aviso?",
    ev_yes: "Sí, confirmar",
    ev_no: "No",
    ev_held: "aviso por confirmar",
    login_title: "Iniciar sesión",
    login_user: "Usuario",
    login_pass: "Contraseña",
    login_go: "Entrar",
    logout: "Cerrar sesión",
    login_bad: "Usuario o contraseña incorrectos.",
    login_locked: "Demasiados intentos fallidos; espera unos minutos.",
    login_expired: "Tu sesión expiró. Inicia sesión de nuevo.",
    login_down: "No se pudo conectar con el servidor.",
  },
  en: {
    a_map: "Park map",
    a_route: "Route of the day",
    live_fail: "No connection to /api — falling back to Mock mode.",
    live_on: "Connected to the API · new session",
    no_plan: "No plan yet",
    no_plan_sub: "Tell the assistant about your party and it will build your day.",
    live_static: "This screen shows illustrative data: the API does not expose it yet.",
    stat_wait: "min waiting",
    a11y_live: "Every stop passed the constraint gate before being proposed. Accessibility fails closed.",
    moved_meta: "moved in the schedule",
    ev_confirm: "Do you confirm this report?",
    ev_yes: "Yes, confirm",
    ev_no: "No",
    ev_held: "report to confirm",
    login_title: "Sign in",
    login_user: "Username",
    login_pass: "Password",
    login_go: "Sign in",
    logout: "Sign out",
    login_bad: "Wrong username or password.",
    login_locked: "Too many failed attempts; wait a few minutes.",
    login_expired: "Your session expired. Please sign in again.",
    login_down: "Could not reach the server.",
  },
  pt: {
    a_map: "Mapa do parque",
    a_route: "Rota do dia",
    live_fail: "Sem conexão com /api — voltando ao modo Mock.",
    live_on: "Conectado à API · nova sessão",
    no_plan: "Ainda não há plano",
    no_plan_sub: "Conte ao assistente sobre o seu grupo e ele monta o seu dia.",
    live_static: "Esta tela usa dados ilustrativos: a API ainda não os expõe.",
    stat_wait: "min de espera",
    a11y_live: "Cada parada passou pela porta de restrições antes de ser proposta. Acessibilidade falha fechado.",
    moved_meta: "movida no horário",
    ev_confirm: "Você confirma este aviso?",
    ev_yes: "Sim, confirmar",
    ev_no: "Não",
    ev_held: "aviso a confirmar",
    login_title: "Entrar",
    login_user: "Usuário",
    login_pass: "Senha",
    login_go: "Entrar",
    logout: "Sair",
    login_bad: "Usuário ou senha incorretos.",
    login_locked: "Muitas tentativas falhas; aguarde alguns minutos.",
    login_expired: "Sua sessão expirou. Entre novamente.",
    login_down: "Não foi possível conectar ao servidor.",
  },
  fr: {
    a_map: "Plan du parc",
    a_route: "Itinéraire du jour",
    live_fail: "Pas de connexion à /api — retour au mode Mock.",
    live_on: "Connecté à l'API · nouvelle session",
    no_plan: "Pas encore de plan",
    no_plan_sub: "Parlez de votre groupe à l'assistant et il construira votre journée.",
    live_static: "Cet écran montre des données illustratives : l'API ne les expose pas encore.",
    stat_wait: "min d'attente",
    a11y_live: "Chaque arrêt a passé la porte des contraintes avant d'être proposé. L'accessibilité échoue fermée.",
    moved_meta: "déplacé dans l'horaire",
    ev_confirm: "Confirmez-vous ce signalement ?",
    ev_yes: "Oui, confirmer",
    ev_no: "Non",
    ev_held: "signalement à confirmer",
    login_title: "Se connecter",
    login_user: "Utilisateur",
    login_pass: "Mot de passe",
    login_go: "Entrer",
    logout: "Se déconnecter",
    login_bad: "Utilisateur ou mot de passe incorrect.",
    login_locked: "Trop de tentatives échouées ; patientez quelques minutes.",
    login_expired: "Votre session a expiré. Reconnectez-vous.",
    login_down: "Impossible de joindre le serveur.",
  },
};

export function readLang(): Lang {
  try {
    const stored = localStorage.getItem("pm_lang");
    if (stored && stored in I18N) return stored as Lang;
  } catch {
    /* storage unavailable */
  }
  return "es";
}

export function saveLang(lang: Lang): void {
  try {
    localStorage.setItem("pm_lang", lang);
  } catch {
    /* storage unavailable */
  }
}

export type TFn = (key: string, vars?: Record<string, string | number>) => string;
export type LocFn = (v: Loc | null | undefined) => string;

export function makeT(lang: Lang): TFn {
  return (key, vars) => {
    let s = I18N[lang][key] ?? EXTRA[lang][key] ?? I18N.es[key] ?? EXTRA.es[key] ?? key;
    if (vars) for (const [a, b] of Object.entries(vars)) s = s.replace(`{${a}}`, String(b));
    return s;
  };
}

/** Domain text travels localized: fixtures hold the four versions, API text is a plain string. */
export function makeLoc(lang: Lang): LocFn {
  return (v) => (v == null ? "" : typeof v === "string" ? v : (v[lang] ?? v.es ?? ""));
}

export function extraLoc(key: string): Loc {
  return Object.fromEntries(LANGS.map((l) => [l, EXTRA[l][key] ?? ""])) as Loc;
}
