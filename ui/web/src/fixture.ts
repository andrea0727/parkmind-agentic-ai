// Illustrative fixture, copied from ui/prototype/index.html. It is only used in mock mode
// and for the park map, ride list, tickets and evaluation screens, which have no API yet.
import type { Fixture } from "./types";

export const FIXTURE: Fixture = {
  guest_profile: { guest_id:"g_mk_001", display_name:"Andrea", party_size:3 },
  party: [
    { name:"Andrea", role:"organizer", note:{es:"Tú",en:"You",pt:"Você",fr:"Vous"} },
    { name:"Carmen", role:"adult",     note:{es:"Silla de ruedas",en:"Wheelchair",pt:"Cadeira de rodas",fr:"Fauteuil roulant"} },
    { name:"Tomás",  role:"child",     note:{es:"8 años · montañas rusas",en:"8 years old · roller coasters",
                                             pt:"8 anos · montanhas-russas",fr:"8 ans · montagnes russes"} }
  ],
  /* PartyConstraints (§33) no lleva accesibilidad ni preferencias [C15, C19]:
     la accesibilidad es AccessibilityRequirements por invitado (carga por
     sesión, nunca en el estado del grafo) y las preferencias blandas son
     GuestProfile. Aquí van separadas por esa razón. */
  party_constraints: { party_size:3, departure_time:"18:00" },
  accessibility_requirements: {
    guest_id:"Carmen", consent:true, retention_policy:"session_only",
    flags:[
      { code:"wheelchair_access", mandatory:true,
        label:{es:"Silla de ruedas",en:"Wheelchair access",pt:"Cadeira de rodas",fr:"Fauteuil roulant"} },
      { code:"no_stairs", mandatory:true,
        label:{es:"Sin escaleras",en:"Step-free",pt:"Sem escadas",fr:"Sans escaliers"} }
    ]
  },
  guest_preferences: {
    soft:[
      { code:"thrill", weight:0.9, label:{es:"Montañas rusas",en:"Roller coasters",pt:"Montanhas-russas",fr:"Montagnes russes"} },
      { code:"shade",  weight:0.5, label:{es:"Evitar sol del mediodía",en:"Avoid midday sun",pt:"Evitar sol do meio-dia",fr:"Éviter le soleil de midi"} },
      { code:"shows",  weight:0.4, label:{es:"Espectáculos",en:"Shows",pt:"Espetáculos",fr:"Spectacles"} }
    ]
  },
  /* IDEA FUTURA — FUERA DE ALCANCE DEL MVP.
     Nada de este bloque existe en §33, en el optimizador ni en el backlog.
     Se dibuja para ilustrar cómo se verían los entitlements SI algún día se
     modelaran; hoy el optimizador no los conoce y no los respeta.
     Ojo en particular:
       · Park Hopper y Lightning Lane no son restricciones del planner.
       · La estatura NO sale del tipo de ticket: sale de guest.height_cm
         (regla 2 del ConstraintChecker).
     Los nombres de producto son de Walt Disney World y se muestran solo como
     contexto del dominio, no como funcionalidad de ParkMind. */
  tickets: {
    product:"3-Day Base Ticket",
    product_key:"tk_base_3",
    park_hopper:false,
    valid_from:"2026-10-09", valid_to:"2026-10-11",
    day_index:1, day_total:3,
    lightning_lane:{ type:"Multi Pass", selections_total:3, selections_used:1 },
    holders:[
      { name:"Andrea", kind:"adult" },
      { name:"Carmen", kind:"adult" },
      { name:"Tomás",  kind:"child" }
    ]
  },
  plan: {
    /* Aurora Park es el parque ficticio del proyecto. El catálogo, las esperas
       y las coordenadas se obtienen de ThemeParks.wiki usando el id de abajo,
       así que los nombres de atracción y la geometría del mapa son reales. */
    plan_id:"pl_7f31", park:"Aurora Park",
    park_id:"75ea578a-adc8-4116-a54d-dccb60765ef9",
    status:"valid", rules_checked:11,
    events:[
      { event_id:"e1", start:"10:55", title:"Space Mountain",       meta:{es:"espera 25 min · acceso en silla de ruedas",en:"25 min wait · wheelchair accessible",pt:"espera 25 min · acesso para cadeira de rodas",fr:"25 min d'attente · accès fauteuil"}, kind:"ride", state:"current",  location:{lat:28.4189,lon:-81.5775}, lightning_lane:true },
      { event_id:"e2", start:"12:10", title:"Be Our Guest",         meta:{es:"almuerzo · bajo techo · 45 min",en:"lunch · indoors · 45 min",pt:"almoço · coberto · 45 min",fr:"déjeuner · en intérieur · 45 min"},            kind:"meal", state:"upcoming", location:{lat:28.4203,lon:-81.5796} },
      { event_id:"e3", start:"13:20", title:"it's a small world",   meta:{es:"bajo techo · espera 10 min",en:"indoors · 10 min wait",pt:"coberto · espera 10 min",fr:"en intérieur · 10 min d'attente"},                kind:"ride", state:"upcoming", location:{lat:28.4205,lon:-81.5805} },
      { event_id:"e4", start:"15:10", title:"Jungle Cruise",        meta:{es:"al aire libre · espera 20 min",en:"outdoors · 20 min wait",pt:"ao ar livre · espera 20 min",fr:"en plein air · 20 min d'attente"},             kind:"ride", state:"upcoming", location:{lat:28.4183,lon:-81.5836} },
      { event_id:"e5", start:"16:00", title:"Big Thunder Mountain", meta:{es:"al aire libre · espera 15 min",en:"outdoors · 15 min wait",pt:"ao ar livre · espera 15 min",fr:"en plein air · 15 min d'attente"},             kind:"ride", state:"upcoming", location:{lat:28.4196,lon:-81.5843}, lightning_lane:true },
      { event_id:"e6", start:"17:10", title:"Cinderella Castle",    meta:{es:"cierre del día · 20 min",en:"end of the day · 20 min",pt:"encerramento do dia · 20 min",fr:"fin de journée · 20 min"},                   kind:"view", state:"upcoming", location:{lat:28.4192,lon:-81.5812} }
    ]
  },
  proposal: {
    proposal_id:"pr_a18c",
    trigger:{es:"Lluvia 60% desde las 15:00 y cierre temporal de Jungle Cruise.",
             en:"60% chance of rain from 3 PM and a temporary closure at Jungle Cruise.",
             pt:"60% de chuva a partir das 15:00 e fechamento temporário da Jungle Cruise.",
             fr:"60% de pluie dès 15h et fermeture temporaire de Jungle Cruise."},
    explanation:{es:"Jungle Cruise es al aire libre y cierra por clima. Haunted Mansion está bajo techo, tiene acceso en silla de ruedas y queda a 180 m de tu parada anterior. Big Thunder Mountain se corre 40 minutos para no comprometer la salida a las 18:00.",
                 en:"Jungle Cruise is outdoors and closes in bad weather. Haunted Mansion is indoors, wheelchair accessible and sits 180 m from your previous stop. Big Thunder Mountain moves 40 minutes later so the 6 PM exit still holds.",
                 pt:"A Jungle Cruise é ao ar livre e fecha por causa do clima. A Haunted Mansion é coberta, tem acesso para cadeira de rodas e fica a 180 m da sua parada anterior. A Big Thunder Mountain atrasa 40 minutos para não comprometer a saída às 18:00.",
                 fr:"Jungle Cruise est en plein air et ferme à cause de la météo. Haunted Mansion est en intérieur, accessible en fauteuil et se trouve à 180 m de votre arrêt précédent. Big Thunder Mountain est décalée de 40 minutes pour préserver le départ à 18h."},
    provenance:{es:"Clima: Open-Meteo (pronóstico horario, 14:40). Estado de atracciones: ThemeParks.wiki (14:52). Distancias: haversine sobre coordenadas de ThemeParks.wiki. Decisión: Replanner + ConstraintChecker, 11 reglas.",
                en:"Weather: Open-Meteo (hourly forecast, 2:40 PM). Attraction status: ThemeParks.wiki (2:52 PM). Distances: haversine over ThemeParks.wiki coordinates. Decision: Replanner + ConstraintChecker, 11 rules.",
                pt:"Clima: Open-Meteo (previsão horária, 14:40). Status das atrações: ThemeParks.wiki (14:52). Distâncias: haversine sobre coordenadas da ThemeParks.wiki. Decisão: Replanner + ConstraintChecker, 11 regras.",
                fr:"Météo : Open-Meteo (prévision horaire, 14h40). État des attractions : ThemeParks.wiki (14h52). Distances : haversine sur les coordonnées ThemeParks.wiki. Décision : Replanner + ConstraintChecker, 11 règles."},
    diff:{
      removed:[{ ref:"e4", start:"15:10", title:"Jungle Cruise",
        meta:{es:"al aire libre · cerrado por clima",en:"outdoors · closed for weather",pt:"ao ar livre · fechado por causa do clima",fr:"en plein air · fermé pour météo"} }],
      added:  [{ ref:"e7", start:"15:10", title:"Haunted Mansion", kind:"ride", location:{lat:28.4205,lon:-81.5833},
        meta:{es:"bajo techo · espera 15 min · acceso silla de ruedas",en:"indoors · 15 min wait · wheelchair accessible",
              pt:"coberto · espera 15 min · acesso para cadeira de rodas",fr:"en intérieur · 15 min d'attente · accès fauteuil"} }],
      moved:  [{ ref:"e5", from:"16:00", to:"16:40", title:"Big Thunder Mountain",
        meta:{es:"movido para mantener la salida a las 18:00",en:"moved to protect the 6 PM exit",
              pt:"movida para manter a saída às 18:00",fr:"décalée pour préserver le départ à 18h"} }]
    },
    metrics:{ unchanged:"4/6", walk_delta:"+12", affinity:"92%",
      unchanged_label:{es:"paradas sin cambio",en:"stops unchanged",pt:"paradas sem mudança",fr:"arrêts inchangés"},
      walk_label:{es:"min caminando",en:"min walking",pt:"min caminhando",fr:"min de marche"},
      affinity_label:{es:"afinidad con tus gustos",en:"match with your tastes",pt:"afinidade com seus gostos",fr:"affinité avec vos goûts"} },
    accessibility_verified:true,
    accessibility_note:{es:"Ruta sin escaleras y acceso en silla de ruedas confirmados en las 6 paradas.",
                        en:"Step-free route and wheelchair access confirmed across all 6 stops.",
                        pt:"Rota sem escadas e acesso para cadeira de rodas confirmados nas 6 paradas.",
                        fr:"Itinéraire sans escaliers et accès fauteuil confirmés sur les 6 arrêts."}
  },
  rides:[
    { kind:"ride", name:"Space Mountain",            zone:"Tomorrowland",   wait:25,   access:true,  loc:{lat:28.4189,lon:-81.5775} },
    { kind:"ride", name:"TRON Lightcycle Run",       zone:"Tomorrowland",   wait:85,   access:true,  loc:{lat:28.4196,lon:-81.5773} },
    { kind:"ride", name:"Buzz Lightyear",            zone:"Tomorrowland",   wait:30,   access:true,  loc:{lat:28.4186,lon:-81.5782} },
    { kind:"ride", name:"Tomorrowland Speedway",     zone:"Tomorrowland",   wait:40,   access:false, loc:{lat:28.4197,lon:-81.5780} },
    { kind:"ride", name:"Haunted Mansion",           zone:"Liberty Square", wait:15,   access:true,  loc:{lat:28.4205,lon:-81.5833} },
    { kind:"ride", name:"Liberty Square Riverboat",  zone:"Liberty Square", wait:20,   access:true,  loc:{lat:28.4202,lon:-81.5840} },
    { kind:"ride", name:"Jungle Cruise",             zone:"Adventureland",  wait:null, access:true,  loc:{lat:28.4183,lon:-81.5836}, closed:true },
    { kind:"ride", name:"Pirates of the Caribbean",  zone:"Adventureland",  wait:20,   access:true,  loc:{lat:28.4181,lon:-81.5839} },
    { kind:"ride", name:"Magic Carpets",             zone:"Adventureland",  wait:10,   access:true,  loc:{lat:28.4180,lon:-81.5832} },
    { kind:"ride", name:"Big Thunder Mountain",      zone:"Frontierland",   wait:15,   access:true,  loc:{lat:28.4196,lon:-81.5843} },
    { kind:"ride", name:"Tiana's Bayou Adventure",   zone:"Frontierland",   wait:75,   access:false, loc:{lat:28.4192,lon:-81.5847} },
    { kind:"ride", name:"Seven Dwarfs Mine Train",   zone:"Fantasyland",    wait:70,   access:false, loc:{lat:28.4201,lon:-81.5790} },
    { kind:"ride", name:"it's a small world",        zone:"Fantasyland",    wait:10,   access:true,  loc:{lat:28.4205,lon:-81.5805} },
    { kind:"ride", name:"Dumbo the Flying Elephant", zone:"Fantasyland",    wait:5,    access:true,  loc:{lat:28.4199,lon:-81.5793} },
    { kind:"ride", name:"Peter Pan's Flight",        zone:"Fantasyland",    wait:55,   access:false, loc:{lat:28.4203,lon:-81.5803} },
    { kind:"ride", name:"Mad Tea Party",             zone:"Fantasyland",    wait:10,   access:true,  loc:{lat:28.4200,lon:-81.5786} },
    { kind:"meal", name:"Be Our Guest",              zone:"Fantasyland",    wait:0,    access:true,  loc:{lat:28.4203,lon:-81.5796} },
    { kind:"view", name:"Cinderella Castle",         zone:"Fantasyland",    wait:0,    access:true,  loc:{lat:28.4192,lon:-81.5812} },
    { kind:"transport", name:"Main Street Vehicles",      zone:"Main Street",    wait:5,    access:true,  loc:{lat:28.4172,lon:-81.5812} }
  ],
  alerts:[
    { id:"al_park", kind:"info",   icon:"🕕", mins:12, title_key:"park_closing", body_key:"park_closing_b" },
    { id:"al_ride", kind:"urgent", icon:"⏳", mins:4,  title_key:"ride_closing", body_key:"ride_closing_b" }
  ]
};
