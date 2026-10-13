"""Chat language: detect English or Spanish from what a guest wrote, and the planner's wording in both.

Detection is a small deterministic heuristic (no model call): it counts words and
characters typical of each language. A message that says too little to tell
("1.05 m", "ok") keeps the language of the conversation so far.
"""

import re
from typing import Literal, get_args

Language = Literal["en", "es"]
DEFAULT_LANGUAGE: Language = "en"
SUPPORTED_LANGUAGES: tuple[str, ...] = get_args(Language)

_WORD_RE = re.compile(r"[a-záéíóúüñ']+")
_SPANISH_CHARS = frozenset("áéíóúüñ¿¡")

_SPANISH_WORDS = frozenset(
    ["el", "la", "los", "las", "un", "una", "unos", "unas", "del", "que", "y", "en", "con", "para", "por", "mi", "mis", "nuestro", "nuestra", "nuestros", "nuestras", "somos", "soy", "es", "está", "están", "estamos", "estoy", "tenemos", "tengo", "tiene", "tienen", "quiero", "queremos", "quisiera", "quisiéramos", "hola", "gracias", "favor", "niño", "niña", "niños", "niñas", "hijo", "hija", "hijos", "hijas", "años", "año", "adulto", "adultos", "salir", "salimos", "salgo", "vamos", "voy", "hasta", "pero", "muy", "más", "menos", "también", "tarde", "mañana", "noche", "mediodía", "parque", "atracciones", "atracción", "montaña", "evitar", "altura", "mide", "mido", "metros", "centímetros", "comer", "almuerzo", "almorzar", "silla", "ruedas", "caminar", "cansado", "cansada", "cansados", "hay", "puede", "pueden", "podemos", "puedo", "cuando", "donde", "nos", "nosotros", "ella", "él", "su", "sus", "lo", "se", "al", "esta", "este", "eso", "esto", "sobre", "hacia", "desde", "entre", "dos", "tres", "cuatro", "cinco", "seis", "familia", "pareja", "amigos", "edad", "esposa", "esposo", "mamá", "papá", "abuelo", "abuela", "sin", "ni", "sí", "aunque", "porque", "llegamos", "llegar", "sobrino", "sobrina", "de", "me", "te", "como", "cómo", "qué", "cuántos", "cuántas"]
)
_ENGLISH_WORDS = frozenset(
    ["the", "and", "we", "are", "is", "have", "has", "our", "my", "with", "for", "to", "of", "in", "kids", "children", "child", "adult", "adults", "want", "wants", "would", "like", "leave", "years", "old", "tall", "park", "please", "hello", "hi", "hey", "not", "don't", "do", "can't", "cannot", "can", "need", "needs", "lunch", "ride", "rides", "but", "also", "at", "by", "from", "about", "around", "he", "she", "they", "you", "your", "it", "this", "that", "an", "just", "yes", "thanks", "thank", "wheelchair", "walk", "walking", "am", "pm", "i", "i'm", "we're", "it's", "or", "so", "as", "be", "will", "arrive", "arriving", "family", "couple", "friends", "wife", "husband", "mom", "dad", "grandpa", "grandma", "daughter", "before", "after", "tired", "avoid", "must", "definitely"]
)


def detect_language(text: str, fallback: Language = DEFAULT_LANGUAGE) -> Language:
    """``"es"`` or ``"en"`` for the text; ``fallback`` when it gives no signal."""
    lowered = text.lower()
    spanish = sum(1 for ch in lowered if ch in _SPANISH_CHARS) * 2
    english = 0
    for word in _WORD_RE.findall(lowered):
        spanish += word in _SPANISH_WORDS
        english += word in _ENGLISH_WORDS
    if spanish == english:
        return fallback
    return "es" if spanish > english else "en"


def as_language(value: object, fallback: Language = DEFAULT_LANGUAGE) -> Language:
    """``value`` if it is a supported language (e.g. a UI locale such as ``es``), else ``fallback``."""
    return value if value in SUPPORTED_LANGUAGES else fallback  # type: ignore[return-value]


_CATALOG: dict[str, dict[Language, str]] = {
    # elicitation questions
    "q_guests": {
        "en": "Who is in your party (how many people, and are they adults or children)?",
        "es": "¿Quiénes forman su grupo (cuántas personas, y son adultos o niños)?",
    },
    "q_departure_time": {
        "en": "What time do you plan to leave the park?",
        "es": "¿A qué hora piensan salir del parque?",
    },
    "q_unreadable_response": {
        "en": "I couldn't understand that. Could you describe your party and plans again?",
        "es": "No pude entender eso. ¿Pueden describir de nuevo a su grupo y sus planes?",
    },
    "q_guest_role": {
        "en": "Is {who} an adult or a child?",
        "es": "¿{who} es adulto o niño?",
    },
    "q_unknown_attraction": {
        "en": "I couldn't find '{name}' among this park's attractions. Which attraction do you mean?",
        "es": "No encontré '{name}' entre las atracciones de este parque. ¿A qué atracción se refieren?",
    },
    "q_ambiguous_attraction": {
        "en": "'{name}' could be {options}. Which one do you mean?",
        "es": "'{name}' podría ser {options}. ¿Cuál de ellas?",
    },
    "q_unverified_height": {
        "en": (
            "I couldn't match the height I understood for Guest {guest} to what you wrote. "
            "How tall is Guest {guest}, in cm (between 50 and 250)?"
        ),
        "es": (
            "No pude relacionar la estatura que entendí de la Persona {guest} con lo que escribieron. "
            "¿Cuánto mide la Persona {guest}, en cm (entre 50 y 250)?"
        ),
    },
    "q_conflicting_attraction": {
        "en": (
            "'{name}' is on both your must-do and your avoid list. "
            "Do you want it in the plan, or kept out of it?"
        ),
        "es": (
            "'{name}' está en su lista de imprescindibles y también en la de evitar. "
            "¿La quieren en el plan o fuera de él?"
        ),
    },
    "q_generic": {
        "en": "Could you tell me about {key}?",
        "es": "¿Pueden contarme sobre {key}?",
    },
    "what_to_change": {
        "en": "What should I change?",
        "es": "¿Qué debo cambiar?",
    },
    # confirmation echoes
    "echo_must_do": {
        "en": "'{value}' is a must-do: the plan has to include it.",
        "es": "'{value}' es imprescindible: el plan debe incluirla.",
    },
    "echo_avoid": {
        "en": "'{value}' is off-limits: the plan must never include it.",
        "es": "'{value}' queda descartada: el plan nunca debe incluirla.",
    },
    "echo_departure_time": {
        "en": "You leave the park at {value}: the plan must end by then.",
        "es": "Salen del parque a las {value}: el plan debe terminar para entonces.",
    },
    "echo_lunch_window": {
        "en": "Lunch is fixed between {start} and {end}.",
        "es": "El almuerzo es entre las {start} y las {end}.",
    },
    "echo_walking_budget": {
        "en": "The party's total walking must not exceed {value} minutes.",
        "es": "La caminata total del grupo no debe pasar de {value} minutos.",
    },
    "echo_height": {
        "en": (
            "Guest {guest} is {value} cm tall (about {feet}): "
            "rides with a higher minimum will be skipped."
        ),
        "es": (
            "La Persona {guest} mide {value} cm: "
            "se omitirán las atracciones con una estatura mínima mayor."
        ),
    },
    # planning stage
    "superseded": {
        "en": (
            "Something in the park changed while you were deciding, so this proposal was "
            "set aside and nothing was activated."
        ),
        "es": (
            "Algo cambió en el parque mientras decidían, así que esta propuesta se dejó "
            "de lado y no se activó nada."
        ),
    },
    "keeps_plan": {
        "en": "Your current plan stays as it was.",
        "es": "Su plan actual sigue como estaba.",
    },
    "ask_again": {
        "en": "Ask me for a new plan and I'll build it from the latest park information.",
        "es": "Pídanme un plan nuevo y lo armaré con la información más reciente del parque.",
    },
    "unavailable": {
        "en": (
            "I couldn't put a plan together right now because some of the park information "
            "I need isn't available. Nothing was proposed. Please try again in a few minutes."
        ),
        "es": (
            "No pude armar un plan ahora porque falta información del parque que necesito. "
            "No se propuso nada. Intenten de nuevo en unos minutos."
        ),
    },
    "no_valid_plan": {
        "en": "I couldn't build a plan that meets every requirement, so nothing was proposed.",
        "es": "No pude armar un plan que cumpla todos los requisitos, así que no propuse nada.",
    },
    "relax_hint": {
        "en": (
            "You can relax a constraint (for example the must-dos or the departure time) "
            "and I'll try again."
        ),
        "es": (
            "Pueden flexibilizar una restricción (por ejemplo los imprescindibles o la hora "
            "de salida) y lo intento de nuevo."
        ),
    },
    "initial_plan_reason": {
        "en": "Initial plan for the party",
        "es": "Plan inicial para el grupo",
    },
    # replanning
    "nothing_to_replan": {
        "en": (
            "There is no confirmed plan for this party yet, so there is nothing to adjust. "
            "Ask me for a plan first."
        ),
        "es": (
            "Todavía no hay un plan confirmado para este grupo, así que no hay nada que "
            "ajustar. Pídanme un plan primero."
        ),
    },
    "replan_not_proposed": {
        "en": "I couldn't build an updated plan that meets every requirement, so nothing was proposed.",
        "es": "No pude armar un plan actualizado que cumpla todos los requisitos, así que no propuse nada.",
    },
    "unmet_must_do": {
        "en": "Must-dos that no longer fit: {names}",
        "es": "Imprescindibles que ya no caben: {names}",
    },
    "reason_none": {
        "en": "The plan was updated after a change in the park.",
        "es": "El plan se actualizó tras un cambio en el parque.",
    },
    "reason_prefix": {
        "en": "Updated plan because {phrases}.",
        "es": "Plan actualizado porque {phrases}.",
    },
    "reason_and": {"en": " and ", "es": " y "},
    "event_attraction_fallback": {"en": "an attraction", "es": "una atracción"},
    "event_down": {"en": "{name} is down", "es": "{name} está fuera de servicio"},
    "event_available": {
        "en": "{name} is available again",
        "es": "{name} vuelve a estar disponible",
    },
    "event_wait_spike": {
        "en": "the wait at {name} jumped{jump}",
        "es": "la espera en {name} subió{jump}",
    },
    "event_wait_spike_by": {"en": " by {minutes} min", "es": " {minutes} min"},
    "event_rain": {"en": "rain is coming", "es": "se acerca lluvia"},
    "event_behind": {
        "en": "the party is{late} behind schedule",
        "es": "el grupo va{late} retrasado",
    },
    "event_behind_by": {"en": " {minutes} min", "es": " {minutes} min"},
    "event_fatigue": {"en": "a guest is tired", "es": "alguien está cansado"},
    # plan explanation
    "guest_label": {"en": "Guest {number}", "es": "Persona {number}"},
    "plan_summary": {
        "en": "{stops} stops, {wait} min of waiting and {walk} min of walking in total.",
        "es": "{stops} paradas, {wait} min de espera y {walk} min de caminata en total.",
    },
    "plan_stop": {
        "en": "{time} {name} ({wait} min wait, {walk} min walk)",
        "es": "{time} {name} ({wait} min de espera, {walk} min caminando)",
    },
    "plan_lunch_tbd": {
        "en": "Lunch (restaurant to be chosen, near your previous stop)",
        "es": "Almuerzo (restaurante por elegir, cerca de su parada anterior)",
    },
    "plan_skip": {
        "en": "{who} will skip {name} (minimum height {limit} cm) and wait outside.",
        "es": "{who} no subirá a {name} (estatura mínima {limit} cm) y esperará afuera.",
    },
    "plan_unmet": {
        "en": "Could not fit your must-dos today: {names}.",
        "es": "No pudieron caber hoy sus imprescindibles: {names}.",
    },
    "diff_added": {"en": "Added", "es": "Agregado"},
    "diff_removed": {"en": "Removed", "es": "Quitado"},
    "diff_reordered": {"en": "Reordered", "es": "Reordenado"},
    "diff_changed": {"en": "Changed", "es": "Cambiado"},
    "diff_line": {"en": "{title}: {items}.", "es": "{title}: {items}."},
    "diff_times_only": {
        "en": "No stops changed; only times shifted.",
        "es": "No cambió ninguna parada; solo se movieron los horarios.",
    },
}


def text(key: str, lang: Language = DEFAULT_LANGUAGE, /, **params: object) -> str:
    """The planner's wording for ``key`` in ``lang``, with ``params`` filled in."""
    return _CATALOG[key][lang].format(**params)
