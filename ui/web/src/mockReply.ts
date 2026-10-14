import { FIXTURE } from "./fixture";
import { haversine, walkMin } from "./geo";
import type { Lang, LocFn, TFn } from "./i18n";
import type { PlanEvent, Reply } from "./types";

interface Ctx {
  lang: Lang;
  t: TFn;
  loc: LocFn;
  plan: PlanEvent[];
}

type L4 = Record<Lang, string>;

/* Canned replies of the prototype, used when there is no API (mock mode). */
export function mockReply(text: string, { lang, t, loc, plan }: Ctx): Reply {
  const s = text.toLowerCase();
  const has = (...w: string[]) => w.some((x) => s.includes(x));
  const pick = (o: L4) => o[lang];

  if (has("silla", "wheelchair", "cadeira", "fauteuil", "mamá", "mom", "mãe", "mère"))
    return {
      text: pick({
        es: "Listo. Guardé esto para tu grupo:",
        en: "Got it. Saved this for your party:",
        pt: "Pronto. Salvei isto para o seu grupo:",
        fr: "C'est noté. Enregistré pour votre groupe :",
      }),
      constraints: [
        { kind: "hard", label: "♿ " + loc(FIXTURE.accessibility_requirements.flags[0].label) },
        { kind: "hard", label: loc(FIXTURE.accessibility_requirements.flags[1].label) },
        { kind: "hard", label: "⏱ " + FIXTURE.party_constraints.departure_time },
        { kind: "soft", label: loc(FIXTURE.guest_preferences.soft[0].label) },
      ],
      confirm: true,
      consentRequired: true,
    };

  if (
    has("lluvia", "llover", "lloviendo", "rain", "raining", "chuva", "chover", "pluie", "pleuvoir",
      "clima", "tiempo", "weather", "forecast", "météo", "previsão", "15:00", "3 pm", "3pm", "las 3")
  )
    return {
      text: pick({
        es: "Hay 60% de lluvia desde las 15:00 y Jungle Cruise reportó cierre temporal. Preparé un cambio: entra Haunted Mansion, bajo techo y con acceso en silla de ruedas. No lo aplico hasta que lo revises.",
        en: "There's a 60% chance of rain from 3 PM and Jungle Cruise reported a temporary closure. I prepared a change: Haunted Mansion comes in — indoors and wheelchair accessible. I won't apply it until you review it.",
        pt: "Há 60% de chuva a partir das 15:00 e a Jungle Cruise reportou fechamento temporário. Preparei uma mudança: entra a Haunted Mansion, coberta e com acesso para cadeira de rodas. Não aplico até você revisar.",
        fr: "Il y a 60% de pluie dès 15h et Jungle Cruise a signalé une fermeture temporaire. J'ai préparé un changement : Haunted Mansion entre, en intérieur et accessible en fauteuil. Je n'applique rien avant votre revue.",
      }),
      open_proposal: true,
    };

  if (has("caminar", "walk", "caminhar", "marche", "distancia", "distance")) {
    const legs = plan.reduce(
      (a, ev, i) => (i ? a + (haversine(plan[i - 1].location, ev.location) || 0) : 0),
      0,
    );
    const km = (legs / 1000).toFixed(2);
    const min = walkMin(legs);
    return {
      text: pick({
        es: `Tu ruta de hoy son ${km} km, unos ${min} minutos caminando repartidos en el día. Todo el trayecto es sin escaleras.`,
        en: `Today's route is ${km} km, about ${min} minutes of walking spread across the day. The whole route is step-free.`,
        pt: `Sua rota de hoje são ${km} km, cerca de ${min} minutos caminhando ao longo do dia. Todo o trajeto é sem escadas.`,
        fr: `Votre itinéraire fait ${km} km, environ ${min} minutes de marche réparties dans la journée. Tout le parcours est sans escaliers.`,
      }),
      followups: true,
    };
  }

  if (has("fila", "line", "wait", "espera", "attente", "corta", "short", "curta", "courte")) {
    /* Real rides only: no restaurants or viewpoints, and wait 0 means "no posted line". */
    const open = FIXTURE.rides
      .filter((r) => r.kind === "ride" && !r.closed && r.access && (r.wait ?? 0) > 0)
      .sort((a, b) => (a.wait ?? 0) - (b.wait ?? 0))
      .slice(0, 3);
    return {
      text: pick({
        es: "Ahora mismo, las más cortas con acceso en silla de ruedas:",
        en: "Right now, the shortest ones with wheelchair access:",
        pt: "Agora, as mais curtas com acesso para cadeira de rodas:",
        fr: "En ce moment, les plus courtes avec accès fauteuil :",
      }),
      constraints: open.map((r) => ({ kind: "plain" as const, label: `${r.name} · ${r.wait} min` })),
      followups: true,
    };
  }

  if (has("ticket", "entrada", "billet", "ingresso", "pass", "hopper", "lightning")) {
    const k = FIXTURE.tickets;
    const ll = k.lightning_lane;
    const left = ll.selections_total - ll.selections_used;
    const product = t(k.product_key);
    return {
      text: pick({
        es: `Tu entrada es ${product}, día ${k.day_index} de ${k.day_total}, sin Park Hopper, y te quedan ${left} reservas de Lightning Lane. Ojo: esto es informativo — hoy no tengo en cuenta el tipo de entrada al armar tu plan.`,
        en: `Your ticket is a ${product}, day ${k.day_index} of ${k.day_total}, no Park Hopper, with ${left} Lightning Lane selections left. Heads up: this is informational — I don't factor ticket type into your plan yet.`,
        pt: `Seu ingresso é ${product}, dia ${k.day_index} de ${k.day_total}, sem Park Hopper, e restam ${left} reservas de Lightning Lane. Atenção: isto é informativo — ainda não considero o tipo de ingresso ao montar o plano.`,
        fr: `Votre billet est un ${product}, jour ${k.day_index} sur ${k.day_total}, sans Park Hopper, avec ${left} réservations Lightning Lane. À noter : c'est informatif — je ne tiens pas encore compte du type de billet pour votre plan.`,
      }),
      constraints: [
        { kind: "plain", label: k.park_hopper ? t("tk_hopper_yes") : t("tk_hopper_no") },
        { kind: "soft", label: "⚡ " + t("tk_ll_left", { n: left, t: ll.selections_total }) },
      ],
      followups: true,
    };
  }

  if (has("techo", "indoor", "coberto", "intérieur", "interior"))
    return {
      text: pick({
        es: "Bajo techo y a menos de 300 m de donde estás: Be Our Guest, Haunted Mansion y it's a small world. Las tres tienen acceso en silla de ruedas.",
        en: "Indoors and under 300 m from you: Be Our Guest, Haunted Mansion and it's a small world. All three are wheelchair accessible.",
        pt: "Cobertas e a menos de 300 m de você: Be Our Guest, Haunted Mansion e it's a small world. As três têm acesso para cadeira de rodas.",
        fr: "En intérieur et à moins de 300 m : Be Our Guest, Haunted Mansion et it's a small world. Les trois sont accessibles en fauteuil.",
      }),
      followups: true,
    };

  return {
    text: pick({
      es: "Lo anoté. Puedo ajustar el plan con eso, pero cualquier cambio te lo propongo primero — nunca lo aplico solo.",
      en: "Noted. I can adjust the plan with that, but any change gets proposed to you first — I never apply it on my own.",
      pt: "Anotei. Posso ajustar o plano com isso, mas qualquer mudança eu proponho primeiro — nunca aplico sozinho.",
      fr: "C'est noté. Je peux ajuster le plan, mais tout changement vous est proposé d'abord — je ne l'applique jamais seul.",
    }),
    followups: true,
  };
}

/** After the guest confirms; in live mode this is the graph resuming from its confirmation interrupt. */
export function replyAfterConfirm(lang: Lang): Reply {
  return {
    text: {
      es: "Confirmado. Con eso armé el día: empieza por Tomorrowland para evitar la fila de mediodía.",
      en: "Confirmed. With that I built the day: start in Tomorrowland to beat the midday line.",
      pt: "Confirmado. Com isso montei o dia: comece pela Tomorrowland para fugir da fila do meio-dia.",
      fr: "Confirmé. Avec ça j'ai construit la journée : commencez par Tomorrowland pour éviter la file de midi.",
    }[lang],
    plan_preview: true,
    followups: true,
  };
}
