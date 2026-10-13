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
    no_plan_sub: "Cuéntale al asistente qué te gustaría hacer y armará tu día.",
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
    n_plan_prop_t: "Tu plan está listo para revisar", n_plan_ready_t: "Plan agregado", n_plan_ready_b: "Tu plan quedó con {n} paradas.", n_plan_upd_t: "Plan actualizado", n_plan_upd_b: "Aplicaste el cambio. Tu plan tiene {n} paradas.", n_kept_t: "Plan sin cambios", n_kept_b: "Mantuviste tu plan.", n_next_t: "Próxima parada en {n} min", n_next_b: "{title} · {time}", n_end_t: "Tu plan termina pronto", n_end_b: "La última parada, {title}, sale a las {time}.", n_ev_t: "Aviso enviado", n_ev_b: "{what}. El asistente revisará si hace falta cambiar tu plan.", ev_title: "Reportar un cambio", ev_behind: "Voy atrasado", ev_tired: "Estoy cansado/a", ev_wait: "Espera larga", ev_down: "Atracción cerrada", ev_pick: "¿Cuál atracción?", ev_sent: "Aviso enviado al asistente.", ev_unknown: "Esa atracción no está en este parque. Elige una de las paradas de tu plan.", ev_none: "Sin cambios necesarios: tu plan sigue igual.", ev_what_behind: "Vas 15 min atrasado", ev_what_tired: "Reportaste cansancio", ev_what_wait: "Reportaste una espera larga en {title}", ev_what_down: "Reportaste {title} cerrada", kept_bot: "Listo, mantengo tu plan tal como está. Si algo cambia, te aviso.",
    dev_row: "Herramientas de desarrollo", dev_sub: "Registro de llamadas a la API", dev_on: "Debug activado", dev_off: "Debug desactivado", dev_clear: "Limpiar", dev_empty: "Aún no hay llamadas. Usa la app y aparecerán aquí.", dev_req: "Petición", dev_res: "Respuesta", dev_nobody: "(sin cuerpo)", dev_prov: "Origen de los datos",
  },
  en: {
    a_map: "Park map",
    a_route: "Route of the day",
    live_fail: "No connection to /api — falling back to Mock mode.",
    live_on: "Connected to the API · new session",
    no_plan: "No plan yet",
    no_plan_sub: "Tell the assistant what you would like to do and it will build your day.",
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
    n_plan_prop_t: "Your plan is ready to review", n_plan_ready_t: "Plan added", n_plan_ready_b: "Your plan has {n} stops.", n_plan_upd_t: "Plan updated", n_plan_upd_b: "You applied the change. Your plan has {n} stops.", n_kept_t: "Plan unchanged", n_kept_b: "You kept your plan.", n_next_t: "Next stop in {n} min", n_next_b: "{title} · {time}", n_end_t: "Your plan ends soon", n_end_b: "The last stop, {title}, ends at {time}.", n_ev_t: "Report sent", n_ev_b: "{what}. The assistant will check whether your plan needs to change.", ev_title: "Report a change", ev_behind: "Running late", ev_tired: "Feeling tired", ev_wait: "Long wait", ev_down: "Ride closed", ev_pick: "Which attraction?", ev_sent: "Report sent to the assistant.", ev_unknown: "That attraction is not in this park. Pick one of the stops in your plan.", ev_none: "No change needed: your plan stays the same.", ev_what_behind: "You are 15 min behind", ev_what_tired: "You reported tiredness", ev_what_wait: "You reported a long wait at {title}", ev_what_down: "You reported {title} closed", kept_bot: "Done, I'm keeping your plan as it is. If anything changes, I'll let you know.",
    dev_row: "Developer tools", dev_sub: "API call log", dev_on: "Debug on", dev_off: "Debug off", dev_clear: "Clear", dev_empty: "No calls yet. Use the app and they will show up here.", dev_req: "Request", dev_res: "Response", dev_nobody: "(no body)", dev_prov: "Data provenance",
  },
  pt: {
    a_map: "Mapa do parque",
    a_route: "Rota do dia",
    live_fail: "Sem conexão com /api — voltando ao modo Mock.",
    live_on: "Conectado à API · nova sessão",
    no_plan: "Ainda não há plano",
    no_plan_sub: "Conte ao assistente o que você gostaria de fazer e ele monta o seu dia.",
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
    n_plan_prop_t: "Seu plano está pronto para revisar", n_plan_ready_t: "Plano adicionado", n_plan_ready_b: "Seu plano ficou com {n} paradas.", n_plan_upd_t: "Plano atualizado", n_plan_upd_b: "Você aplicou a mudança. Seu plano tem {n} paradas.", n_kept_t: "Plano sem mudanças", n_kept_b: "Você manteve seu plano.", n_next_t: "Próxima parada em {n} min", n_next_b: "{title} · {time}", n_end_t: "Seu plano termina em breve", n_end_b: "A última parada, {title}, termina às {time}.", n_ev_t: "Aviso enviado", n_ev_b: "{what}. O assistente vai verificar se é preciso mudar seu plano.", ev_title: "Relatar uma mudança", ev_behind: "Estou atrasado", ev_tired: "Estou cansado(a)", ev_wait: "Espera longa", ev_down: "Atração fechada", ev_pick: "Qual atração?", ev_sent: "Aviso enviado ao assistente.", ev_unknown: "Essa atração não está neste parque. Escolha uma das paradas do seu plano.", ev_none: "Nenhuma mudança necessária: seu plano continua igual.", ev_what_behind: "Você está 15 min atrasado", ev_what_tired: "Você relatou cansaço", ev_what_wait: "Você relatou espera longa em {title}", ev_what_down: "Você relatou {title} fechada", kept_bot: "Pronto, mantenho seu plano como está. Se algo mudar, eu aviso.",
    dev_row: "Ferramentas de desenvolvimento", dev_sub: "Registro de chamadas à API", dev_on: "Debug ativado", dev_off: "Debug desativado", dev_clear: "Limpar", dev_empty: "Ainda não há chamadas. Use o app e elas aparecerão aqui.", dev_req: "Requisição", dev_res: "Resposta", dev_nobody: "(sem corpo)", dev_prov: "Origem dos dados",
  },
  fr: {
    a_map: "Plan du parc",
    a_route: "Itinéraire du jour",
    live_fail: "Pas de connexion à /api — retour au mode Mock.",
    live_on: "Connecté à l'API · nouvelle session",
    no_plan: "Pas encore de plan",
    no_plan_sub: "Dites à l'assistant ce que vous aimeriez faire et il construira votre journée.",
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
    n_plan_prop_t: "Votre plan est prêt à être revu", n_plan_ready_t: "Plan ajouté", n_plan_ready_b: "Votre plan compte {n} arrêts.", n_plan_upd_t: "Plan mis à jour", n_plan_upd_b: "Vous avez appliqué le changement. Votre plan compte {n} arrêts.", n_kept_t: "Plan inchangé", n_kept_b: "Vous avez gardé votre plan.", n_next_t: "Prochain arrêt dans {n} min", n_next_b: "{title} · {time}", n_end_t: "Votre plan se termine bientôt", n_end_b: "Le dernier arrêt, {title}, finit à {time}.", n_ev_t: "Signalement envoyé", n_ev_b: "{what}. L'assistant vérifiera si votre plan doit changer.", ev_title: "Signaler un changement", ev_behind: "Je suis en retard", ev_tired: "Je suis fatigué(e)", ev_wait: "Longue attente", ev_down: "Attraction fermée", ev_pick: "Quelle attraction ?", ev_sent: "Signalement envoyé à l'assistant.", ev_unknown: "Cette attraction n'est pas dans ce parc. Choisissez l'un des arrêts de votre plan.", ev_none: "Aucun changement nécessaire : votre plan reste le même.", ev_what_behind: "Vous avez 15 min de retard", ev_what_tired: "Vous avez signalé de la fatigue", ev_what_wait: "Vous avez signalé une longue attente à {title}", ev_what_down: "Vous avez signalé {title} fermée", kept_bot: "C'est noté, je garde votre plan tel quel. Si quelque chose change, je vous préviens.",
    dev_row: "Outils de développement", dev_sub: "Journal des appels API", dev_on: "Debug activé", dev_off: "Debug désactivé", dev_clear: "Effacer", dev_empty: "Aucun appel pour l'instant. Utilisez l'app et ils apparaîtront ici.", dev_req: "Requête", dev_res: "Réponse", dev_nobody: "(sans corps)", dev_prov: "Provenance des données",
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
