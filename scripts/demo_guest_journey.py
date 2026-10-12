"""Demo: el dia de una familia en Magic Kingdom, de punta a punta, con lo que ParkMind tiene hoy.

    poetry run python scripts/demo_guest_journey.py --pausa    # para presentar: Enter entre pasos
    poetry run python scripts/demo_guest_journey.py            # todo seguido (captura 2026-09-27, sin red)
    poetry run python scripts/demo_guest_journey.py --live     # el parque real de hoy (solo en horario del parque)
    poetry run python scripts/demo_guest_journey.py --no-db    # sin la sección de Postgres

Antes de presentar: `docker compose up -d` (paso 8) y estar en la rama
feature/forecast-service hasta que #72 se mergee (paso 5).

Cada paso es una etapa del flujo de planeacion inicial (Arquitectura seccion 35) y dice:

    En produccion: lo que hace el sistema terminado en este paso
    Aqui:          lo que este script ejecuta de verdad
    CONSTRUIDO (P0-xx)   codigo real y probado de ParkMind
    SUSTITUTO  (P0-yy)   unas lineas de pegamento donde ese item aun no existe
    PENDIENTE  (P0-zz)   aun no construido

Datos por defecto: la captura real de ThemeParks + Open-Meteo del 2026-09-27 ~11:00
(tests/fixtures/snapshot_2026-09-27), servida por los clientes reales con un
transporte simulado: sin red, misma salida en cada corrida (salvo el id del plan).
La guia para el presentador esta en scripts/demo_guest_journey_guion.md.

Local demo helper -- not part of the codebase, do not commit.
"""

import argparse
import json
import logging
import os
import re
import sys
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))  # capture.Provider + in-memory fakes

from pydantic import ValidationError

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    ApprovalStatus,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    Guest,
    GuestProfile,
    GuestRole,
    HardConstraintSet,
    LiveContext,
    MobilityRequirement,
    Park,
    PartyConstraints,
    Plan,
    PlanningPace,
    PlanningStyle,
    PreferenceSource,
    PreferenceValue,
    SensitivityKind,
    SensitivityLevel,
    StopKind,
    TimeWindow,
)
from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.clients.land_reference_data import MAGIC_KINGDOM_LAND_ALIASES
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.routing_client import RoutingClient
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.clients.themeparks_normalize import parse_catalog
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    MAGIC_KINGDOM_EXCLUDED_ENTITIES,
    MAGIC_KINGDOM_SCHEDULED_SHOWS,
)
from parkmind.services.personalization.guest_profile_service import (
    GuestProfileService,
    PreferenceUpdate,
    ProfileUpdate,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import NotFoundError, ProfileVersionConflictError
from parkmind.services.use_cases.check_accessibility import (
    check_accessibility,
    notice_coverage,
)
from parkmind.services.use_cases.collect_snapshot import SnapshotCollector
from parkmind.services.use_cases.latest_snapshot import latest_valid_snapshot

logging.getLogger("parkmind").setLevel(logging.ERROR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(
        encoding="utf-8", errors="replace"
    )  # una consola cp1252/cp850 no debe tumbar la demo

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"
CHILDREN_FIXTURE = os.path.join(
    ROOT, "tests", "fixtures", "themeparks", "children_magic_kingdom_2026-10-01.json"
)
CAPTURE_NOW = datetime(2026, 9, 27, 11, 5, tzinfo=PARK_TZ)
THREAD = "thread_rivera"
WIDTH = 92

PETER_PAN = "86a41273-5f15-4b54-93b6-829f140e5161"
FESTIVAL_PARADE = "ee56b2f3-fd49-4a29-ae1a-2d321549a633"
HAUNTED_MANSION = "2551a77d-023f-4ab1-9a19-8afec0190f39"

ANA, LUIS, SOFIA, CARMEN = "g_ana", "g_luis", "g_sofia", "g_carmen"
WHO = {ANA: "Ana", LUIS: "Luis", SOFIA: "Sofía", CARMEN: "Carmen"}

# --- vocabulario en español para lo que se muestra en pantalla -------------------------
PACE = {"relaxed": "relajado", "balanced": "equilibrado", "maximizer": "maximizador"}
SOURCE = {"stated": "declarado", "learned": "aprendido", "default": "por defecto"}
CATEGORY = {
    "THRILL": "emoción fuerte",
    "FAMILY": "familiar",
    "DARK_RIDE": "dark ride",
    "SHOW": "show",
    "WATER": "agua",
    "CHARACTER": "personajes",
    "TRANSPORT": "transporte",
}
KIND = {"ATTRACTION": "atracción", "SHOW": "show", "MEAL": "comida", "REST": "descanso"}
STATUS = {
    "OPERATING": "operando",
    "DOWN": "caída",
    "CLOSED": "cerrada",
    "REFURBISHMENT": "en remodelación",
}
RULE = {
    "OPENING_HOURS": "1 horario y estado de la atracción",
    "HEIGHT": "2 estatura mínima",
    "SHOW_ARRIVAL": "3 llegar a tiempo al show",
    "MUST_DO": "4 imprescindibles cubiertos",
    "AVOID": "5 nada de lo que se pidio evitar",
    "WALKING_BUDGET": "6 presupuesto de caminata del grupo",
    "LUNCH_WINDOW": "7 almuerzo dentro de su ventana",
    "DEPARTURE": "8 terminar antes de la hora de salida",
    "ACCESSIBILITY": "9 accesibilidad (caminata, descansos, calor)",
    "RIDE_RESTRICTION": "10 avisos de seguridad del parque",
    "DATA_FRESHNESS": "11 datos frescos y completos",
}
RESTRICTION = {
    "REQUIRES_TRANSFER_FROM_WHEELCHAIR": "hay que pasarse de la silla de ruedas",
    "NOT_RECOMMENDED_HIGH_G_FORCE": "fuerzas G altas",
    "NOT_RECOMMENDED_MOTION_SENSITIVITY": "sensibilidad al movimiento",
    "NOT_RECOMMENDED_HEART_CONDITION": "condición cardíaca",
    "NOT_RECOMMENDED_BACK_NECK": "espalda o cuello",
    "NOT_RECOMMENDED_EXPECTANT": "embarazo",
    "USES_SERVICE_ANIMAL": "animal de servicio no permitido",
}


# ---------------------------------------------------------------------------
# narracion
# ---------------------------------------------------------------------------
PAUSE = False


def pause() -> None:
    if PAUSE:
        try:
            input("\n  [Enter para continuar] ")
        except EOFError:
            pass


def say(text: str, indent: str = "  ") -> None:
    """Imprime un parrafo envuelto al ancho de la pantalla."""
    print(
        textwrap.fill(
            text, WIDTH, initial_indent=indent, subsequent_indent=indent + "  "
        )
    )


def header(title: str) -> None:
    print(f"\n{'=' * WIDTH}\n {title}\n{'=' * WIDTH}")


def step(production: str, here: str, *tags: str) -> None:
    say(f"En producción: {production}")
    say(f"Aquí:          {here}")
    for tag in tags:
        say(tag)
    print()


def key(message: str) -> None:
    print()
    say(f">> Clave: {message}")


def refused(label: str, reason: str, build) -> None:  # type: ignore[no-untyped-def]
    try:
        build()
    except (ValidationError, ValueError) as exc:
        msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        print(f"  RECHAZADO  {label:<42} -> {reason}")
        print(f"             {'':<42}    (validador: {msg})")
    else:
        print(f"  !! ACEPTADO (inesperado)  {label}")


def t(value: datetime) -> str:
    return value.astimezone(PARK_TZ).strftime("%H:%M")


def c_from_f(value: float) -> int:
    return round((value - 32) * 5 / 9)


@dataclass
class Journey:
    """Estado que pasa de un paso al siguiente (en producción lo lleva el estado de LangGraph)."""

    live: bool
    now: datetime = CAPTURE_NOW
    findings: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    catalog: list[Attraction] = field(default_factory=list)
    park: Park | None = None
    guests: list[Guest] = field(default_factory=list)
    constraints: PartyConstraints | None = None
    accessibility: list[AccessibilityRequirements] = field(default_factory=list)
    profiles: dict[str, GuestProfile] = field(default_factory=dict)
    snapshots: Any = None
    id_mappings: Any = None
    context: LiveContext | None = None
    eligible: dict[str, list[str]] = field(default_factory=dict)
    utilities: dict[str, float] = field(default_factory=dict)
    objective: GroupObjective | None = None
    plan: Plan | None = None
    plan_valid: bool = False
    status: dict[str, str] = field(default_factory=dict)

    def name(self, node_id: str, width: int = 44) -> str:
        label = self.names.get(node_id, node_id[:8])
        return label if len(label) <= width else label[: width - 1] + "~"

    def humanize(self, text: str) -> str:
        """Cambia los ids (UUID, g_ana...) de un mensaje por nombres legibles."""
        text = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            lambda m: self.name(m.group(0)),
            text,
        )
        for gid, who in WHO.items():
            text = text.replace(gid, who)
        return text


# ---------------------------------------------------------------------------
# 0. datos del parque que comparten los pasos: catalogo + horario del día
# ---------------------------------------------------------------------------
def _parks_client(j: Journey) -> ThemeParksClient:
    if j.live:
        return ThemeParksClient(MAGIC_KINGDOM)
    from capture import Provider

    return Provider().parks()


def load_park(j: Journey) -> None:
    if j.live:
        j.now = datetime.now(PARK_TZ)  # el script lee el reloj; el núcleo nunca lo hace
        with ThemeParksClient(MAGIC_KINGDOM) as client:
            j.catalog = client.get_catalog()
    else:
        with open(CHILDREN_FIXTURE, encoding="utf-8") as fh:
            children = json.load(fh)
        j.catalog = parse_catalog(
            children,
            MAGIC_KINGDOM_ATTRACTION_METADATA,
            excluded=frozenset(MAGIC_KINGDOM_EXCLUDED_ENTITIES),
        ).attractions
    j.names = {a.node_id: a.name for a in j.catalog}
    with _parks_client(j) as client:
        j.park = client.get_schedule(j.now.date())


def intro(j: Journey) -> None:
    assert j.park is not None
    header("ParkMind -- un día en Magic Kingdom con la familia Rivera")
    say(
        f"Datos: {'el parque EN VIVO de hoy' if j.live else 'captura real del 2026-09-27 a las 11:00 (sin red, reproducible)'}. "
        f"Parque abierto {t(j.park.opening_time)}-{t(j.park.closing_time)}; la familia planea a las {t(j.now)}."
    )
    print()
    say(
        "La familia: Ana (prefiere dark rides, no quiere filas largas), Luis (busca emoción fuerte), "
        "Sofía (niña de 110 cm) y la abuela Carmen (silla de ruedas, descanso cada 90 min)."
    )
    print()
    say(
        "Recorrido: 1 pedido -> 2 perfiles -> 3 datos del parque + accesibilidad -> 4 objetivo del grupo -> "
        "5 pronóstico de filas -> 6 plan -> 7 compuerta de 11 reglas -> 8 aprobación y privacidad -> "
        "9 una atracción se cae -> 10 dónde estamos."
    )
    print()
    say(
        "En cada paso verá qué es código real (CONSTRUIDO), qué es pegamento de la demo (SUSTITUTO) "
        "y qué falta (PENDIENTE), con su id del backlog."
    )
    pause()


# ---------------------------------------------------------------------------
# 1. PEDIDO -> restricciones tipadas
# ---------------------------------------------------------------------------
def step1_elicit(j: Journey) -> None:
    header(
        "1. EL PEDIDO -- lo que la familia pide se vuelve restricciones tipadas y validadas"
    )
    step(
        "el Concierge (un LLM) conversa con la familia, extrae estos contratos y les pide confirmar "
        "cada restricción dura (interrupt) antes de planear.",
        "el resultado de esa extracción, escrito a mano.",
        "SUSTITUTO  (P0-29 Concierge / P0-31 interrupt de confirmación)",
        "CONSTRUIDO (P0-05 contratos): todo lo que sigue pasa los validadores de la sección 33",
    )
    assert j.park is not None
    departure = min(
        j.park.closing_time, j.now.replace(hour=18, minute=0, second=0, microsecond=0)
    )
    j.guests = [
        Guest(guest_id=ANA, role=GuestRole.ADULT, height_cm=165),
        Guest(guest_id=LUIS, role=GuestRole.ADULT, height_cm=180),
        Guest(guest_id=SOFIA, role=GuestRole.CHILD, height_cm=110),
        Guest(guest_id=CARMEN, role=GuestRole.ADULT, height_cm=158),
    ]
    j.constraints = PartyConstraints(
        party_size=4,
        guests=j.guests,
        must_do=[PETER_PAN, FESTIVAL_PARADE],
        avoid=[HAUNTED_MANSION],
        lunch_window=TimeWindow(
            start=j.now.replace(hour=12, minute=0, second=0, microsecond=0),
            end=j.now.replace(hour=13, minute=30, second=0, microsecond=0),
        ),
        departure_time=departure,
        party_walking_budget_minutes=180,
        constraints_version=1,
    )
    # Necesidades de accesibilidad: un registro por persona. Las de Carmen son reales; el resto
    # no declara ninguna. session_only = nunca se escribe en una tabla (C19).
    j.accessibility = [
        AccessibilityRequirements(
            guest_id=CARMEN,
            mobility_requirements=[MobilityRequirement.WHEELCHAIR],
            daily_walking_limit_minutes=150,
            rest_frequency_minutes=90,
            consent=True,
            retention_policy="session_only",
        ),
        *(
            AccessibilityRequirements(
                guest_id=g, consent=True, retention_policy="session_only"
            )
            for g in (ANA, LUIS, SOFIA)
        ),
    ]
    role = {"adult": "adulto", "child": "niña"}
    print(
        "  Grupo:           "
        + ", ".join(
            f"{WHO[g.guest_id]} ({role[g.role.value]}, {g.height_cm:.0f} cm)"
            for g in j.guests
        )
    )
    print(f"  Imprescindibles: {j.name(PETER_PAN)}; {j.name(FESTIVAL_PARADE)}")
    print(f"  Evitar:          {j.name(HAUNTED_MANSION)}")
    lunch = j.constraints.lunch_window
    assert lunch is not None
    print(
        f"  Almuerzo:        {t(lunch.start)}-{t(lunch.end)}    Salida: {t(departure)}    "
        f"Caminata del grupo: hasta {j.constraints.party_walking_budget_minutes} min"
    )
    print(
        "  Carmen:          silla de ruedas, camina hasta 150 min/día, descansa cada 90 min (solo en sesión)"
    )

    print(
        "\n  La frontera rechaza lo que nunca debe colarse (ni siquiera si lo propone el LLM):"
    )
    refused(
        "Datos de accesibilidad sin consentimiento",
        "se exige consentimiento explícito",
        lambda: AccessibilityRequirements(
            guest_id=CARMEN,
            mobility_requirements=[MobilityRequirement.WHEELCHAIR],
            consent=False,
        ),
    )
    refused(
        "Una niña de 20 cm",
        "estatura fuera de rango (mínimo 50 cm)",
        lambda: Guest(guest_id="g_x", role=GuestRole.CHILD, height_cm=20),
    )
    refused(
        "Un grupo de 0 personas",
        "el grupo debe tener al menos 1 persona",
        lambda: PartyConstraints.model_validate(
            {**j.constraints.model_dump(), "party_size": 0}
        ),
    )  # type: ignore[union-attr]
    key(
        "el LLM conversa, pero lo que entra al planificador son contratos tipados y validados. "
        "Un dato inválido se rechaza en la frontera, no a mitad del plan."
    )
    j.status["1 pedido"] = "SUSTITUTO (P0-29/31); contratos CONSTRUIDOS (P0-05)"


# ---------------------------------------------------------------------------
# 2. PERFILES
# ---------------------------------------------------------------------------
class _ProfileRepo:
    """ProfileRepository en memoria con la regla de versionado de Postgres (igual al fake de los tests)."""

    def __init__(self, guests: set[str]) -> None:
        self._guests, self._history = guests, {}  # type: ignore[var-annotated]

    def save(self, profile: GuestProfile) -> None:
        if profile.guest_id not in self._guests:
            raise NotFoundError(profile.guest_id)
        history = self._history.setdefault(profile.guest_id, {})
        if history and profile.profile_version <= max(history):
            raise ProfileVersionConflictError("profile_version must increase")
        history[profile.profile_version] = profile

    def get_latest(self, guest_id: str) -> GuestProfile | None:
        history = self._history.get(guest_id)
        return history[max(history)] if history else None

    def get_version(self, guest_id: str, version: int) -> GuestProfile | None:
        return self._history.get(guest_id, {}).get(version)


def step2_profiles(j: Journey) -> None:
    header("2. PERFILES -- versionados, y sabiendo de dónde salió cada preferencia")
    step(
        "GuestProfileService carga el perfil de cada persona desde Postgres. Lo declarado viene del chat, "
        "lo aprendido del comportamiento pasado, y el resto son valores por defecto.",
        "el GuestProfileService real, sobre un repositorio en memoria con la misma regla de versiones.",
        "CONSTRUIDO (P0-14 GuestProfileService)",
    )
    now = j.now

    def pref(
        value: float, source: PreferenceSource = PreferenceSource.DEFAULT
    ) -> PreferenceValue:
        stated = value if source == PreferenceSource.STATED else None
        return PreferenceValue(
            value=value,
            source=source,
            confidence=0.6,
            updated_at=now,
            stated_value=stated,
        )

    service = GuestProfileService(_ProfileRepo({ANA, LUIS, SOFIA, CARMEN}))
    seeds = {
        ANA: (
            PlanningPace.RELAXED,
            pref(0.5),
            pref(0.5),
            [AttractionCategory.DARK_RIDE, AttractionCategory.FAMILY],
            [],
            {},
        ),
        LUIS: (
            PlanningPace.MAXIMIZER,
            pref(0.9, PreferenceSource.STATED),
            pref(0.8),
            [AttractionCategory.THRILL],
            [],
            {},
        ),
        SOFIA: (
            PlanningPace.BALANCED,
            pref(0.4),
            pref(0.5),
            [AttractionCategory.FAMILY, AttractionCategory.CHARACTER],
            [],
            {SensitivityKind.DARKNESS: SensitivityLevel.MEDIUM},
        ),
        CARMEN: (
            PlanningPace.RELAXED,
            pref(0.3),
            pref(0.2),
            [AttractionCategory.SHOW, AttractionCategory.FAMILY],
            [AttractionCategory.THRILL],
            {SensitivityKind.INTENSITY: SensitivityLevel.HIGH},
        ),
    }
    for gid, (pace, queue, walking, likes, dislikes, sens) in seeds.items():
        service.create(
            GuestProfile(
                guest_id=gid,
                pace=pace,
                queue_tolerance=queue,
                walking_tolerance=walking,
                preferred_categories=likes,
                avoided_categories=dislikes,
                sensitivities=sens,
                planning_style=PlanningStyle.FLEXIBLE,
                profile_version=1,
            )
        )
    # En el chat Ana dice "hoy no queremos filas largas" -> una actualizacion DECLARADA.
    updated = service.update(
        ANA,
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.2,
                source=PreferenceSource.STATED,
                confidence=0.95,
                updated_at=now,
            )
        ),
    )
    j.profiles = {gid: service.get(gid) for gid in seeds}  # type: ignore[misc]
    print(
        f"  {'':<7} {'ver':<4} {'ritmo':<12} {'tolera filas':<24} {'tolera caminar':<24} le gusta"
    )
    for gid, p in j.profiles.items():
        q, w = p.queue_tolerance, p.walking_tolerance
        print(
            f"  {WHO[gid]:<7} v{p.profile_version:<3} {PACE[p.pace.value]:<12} "
            f"{q.value:.1f} ({SOURCE[q.source.value]:<11})     {w.value:.1f} ({SOURCE[w.source.value]:<11})     "
            f"{', '.join(CATEGORY[c.value] for c in p.preferred_categories)}"
        )
    v1 = service.get_version(ANA, 1)
    assert v1 is not None
    print(
        f'\n  Ana dijo en el chat "hoy no queremos filas largas": su tolerancia pasó de {v1.queue_tolerance.value:.1f} '
        f"a {updated.queue_tolerance.value:.1f} (declarado) y se creó la v{updated.profile_version}; la v1 sigue consultable."
    )
    key(
        "cada preferencia guarda su origen (declarado / aprendido / por defecto) y cada cambio es una "
        "versión nueva: podemos explicar y auditar por qué el plan quedó como quedó."
    )
    j.status["2 perfiles"] = "CONSTRUIDO (P0-14)"


# ---------------------------------------------------------------------------
# 3. DATOS DEL PARQUE + accesibilidad
# ---------------------------------------------------------------------------
def step3_context(j: Journey) -> None:
    header(
        "3. DATOS DEL PARQUE -- una foto en vivo del parque, y quién puede subir a qué"
    )
    step(
        "un recolector guarda una foto (snapshot) del parque cada 5 min; LOAD CONTEXT toma la última válida, "
        "revisa su cobertura y cruza los avisos de seguridad de Disney con cada persona.",
        "SnapshotCollector con los clientes reales (sobre la captura, o en vivo con --live), "
        "latest_valid_snapshot y check_accessibility sobre el corpus revisado de avisos de Disney.",
        "CONSTRUIDO (P0-07/08 clientes, P0-10 normalización, P0-11 recolector y última foto válida)",
        "CONSTRUIDO (P0-26a avisos de seguridad + check_accessibility)",
        "SUSTITUTO  (P0-30 load_context): copiar los resultados de accesibilidad al contexto",
    )
    from fakes import InMemoryIdMappingRepository, InMemorySnapshotRepository

    j.snapshots, j.id_mappings = (
        InMemorySnapshotRepository(),
        InMemoryIdMappingRepository(),
    )
    if j.live:
        parks, weather = ThemeParksClient(MAGIC_KINGDOM), OpenMeteoClient()
        collected_at = j.now
    else:
        from capture import NOW, Provider

        provider = Provider()
        parks, weather = provider.parks(), provider.weather()
        collected_at = NOW
    result = SnapshotCollector(parks, weather, j.snapshots, j.id_mappings).collect(
        now=collected_at
    )
    latest = latest_valid_snapshot(j.snapshots, now=j.now)
    assert latest is not None
    ctx = latest.live_context
    print(
        f"  Foto del parque tomada a las {t(ctx.retrieved_at)}; edad {latest.age.seconds // 60} min -> "
        f"{'FRESCA' if latest.fresh else 'VENCIDA'} (la regla 11 acepta hasta 30 min). "
        f"Problemas de identidad: {len(result.issues)}"
    )
    counts: dict[str, int] = {}
    for status in ctx.statuses.values():
        counts[STATUS[status.value]] = counts.get(STATUS[status.value], 0) + 1
    print(
        "  Estado:          "
        + ", ".join(
            f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1])
        )
    )
    top = sorted(ctx.waits.values(), key=lambda w: w.wait_minutes, reverse=True)[:4]
    print(
        "  Filas más largas: "
        + "; ".join(
            f"{j.name(w.attraction_id, 32)} {w.wait_minutes:.0f} min" for w in top
        )
    )
    shows = [
        (sid, [s for s in ctx.showtimes.get(sid, []) if s > j.now])
        for sid in MAGIC_KINGDOM_SCHEDULED_SHOWS
    ]
    upcoming = sorted((starts[0], sid) for sid, starts in shows if starts)[:4]
    print(
        "  Próximos shows:  "
        + ("; ".join(f"{t(s)} {j.name(sid, 34)}" for s, sid in upcoming) or "ninguno")
    )
    hours = [
        w for w in ctx.weather if j.now <= w.timestamp <= j.now + timedelta(hours=5)
    ]
    print(
        "  Clima:           "
        + "   ".join(
            f"{t(w.timestamp)} {c_from_f(w.temperature_f)}C lluvia {w.precipitation_probability:.0%}"
            for w in hours
        )
    )
    cov = ctx.coverage
    print(
        f"  Cobertura:       atracciones {'sí' if cov.required_attractions_covered else 'NO'} | "
        f"shows {'sí' if cov.required_shows_covered else 'NO'} | clima {'sí' if cov.weather_covered else 'NO'} | "
        f"accesibilidad {'sí' if cov.accessibility_checks_complete else 'NO (todavía)'}"
    )

    store = magic_kingdom_knowledge_store()
    ids = [a.node_id for a in j.catalog]
    cov_report = notice_coverage(store, ids)
    results = [
        check_accessibility(req, a, store) for req in j.accessibility for a in ids
    ]
    blocked = [r for r in results if not r.eligible]
    print(
        f"\n  Avisos de seguridad (corpus {store.corpus_version}): {len(cov_report.covered)} de {len(ids)} "
        f"atracciones cubiertas. {len(results)} chequeos (4 personas x {len(ids)})."
    )
    by_reason: dict[str, list[str]] = {}
    for r in blocked:
        reason = (
            getattr(r.conflicting_requirement, "value", r.conflicting_requirement)
            or "sin aviso"
        )
        by_reason.setdefault(
            f"{WHO[r.guest_id]} -- {RESTRICTION.get(reason, reason)}", []
        ).append(j.name(r.attraction_id, 60))
    for who_reason, rides in by_reason.items():
        say(
            f"No elegible: {who_reason} ({len(rides)}): {', '.join(sorted(rides))}",
            indent="  ",
        )
    j.context = ctx.model_copy(
        update={
            "accessibility_results": results,
            "coverage": ctx.coverage.model_copy(
                update={"accessibility_checks_complete": True}
            ),
        }
    )
    key(
        "los datos vienen de fuentes reales y tienen fecha: si la foto envejece más de 30 min, el plan no pasa. "
        "Y la accesibilidad falla cerrado: sin aviso de seguridad, no se sube."
    )
    j.status["3 datos del parque"] = (
        "CONSTRUIDO (P0-07/08/10/11, P0-26a); conexión SUSTITUTO (P0-30)"
    )


# ---------------------------------------------------------------------------
# 4. OBJETIVO DEL GRUPO
# ---------------------------------------------------------------------------
_PLANNABLE = {
    AttractionCategory.THRILL,
    AttractionCategory.FAMILY,
    AttractionCategory.DARK_RIDE,
    AttractionCategory.WATER,
    AttractionCategory.CHARACTER,
}


def step4_objective(j: Journey) -> None:
    header("4. OBJETIVO DEL GRUPO -- quién puede subir a qué, y qué valora el grupo")
    step(
        "GroupPreferenceResolver combina los cuatro perfiles en un solo objetivo (equidad, elegibilidad por "
        "persona según reglas 2, 9 y 10) y PreferenceScorer lo convierte en una utilidad por atracción.",
        "unas líneas: elegible = estatura + aviso de seguridad; utilidad = lo que gusta suma, lo que no "
        "gusta resta, y la emoción fuerte pesa menos para quien es sensible a la intensidad.",
        "SUSTITUTO (P0-16 GroupPreferenceResolver, P0-17 PreferenceScorer)",
    )
    assert j.context is not None and j.constraints is not None
    ok = {
        (r.guest_id, r.attraction_id)
        for r in j.context.accessibility_results
        if r.eligible
    }
    heights = {g.guest_id: g.height_cm or 0 for g in j.guests}
    for g in j.guests:
        j.eligible[g.guest_id] = sorted(
            a.node_id
            for a in j.catalog
            if (g.guest_id, a.node_id) in ok
            and (
                a.height_restriction_cm is None
                or heights[g.guest_id] >= a.height_restriction_cm
            )
        )
    operating = {
        nid for nid, s in j.context.statuses.items() if s == AttractionStatus.OPERATING
    }
    for a in j.catalog:
        if (
            a.category not in _PLANNABLE
            or a.node_id not in operating
            or a.node_id in j.constraints.avoid
        ):
            continue
        riders = [g for g in j.eligible if a.node_id in j.eligible[g]]
        if not riders:
            continue
        scores = []
        for gid in riders:
            p = j.profiles[gid]
            score = (
                1.0
                + (0.8 if a.category in p.preferred_categories else 0.0)
                - (0.8 if a.category in p.avoided_categories else 0.0)
            )
            if (
                a.category == AttractionCategory.THRILL
                and p.sensitivities.get(SensitivityKind.INTENSITY)
                == SensitivityLevel.HIGH
            ):
                score -= 0.5
            scores.append(score)
        j.utilities[a.node_id] = round(
            sum(scores) / len(j.guests), 3
        )  # el disfrute de quienes suben, repartido en el grupo
    j.objective = GroupObjective(
        objective_version="demo-standin",
        weights={"lambda_q": 1.0, "lambda_w": 1.0},
        per_guest_eligible=j.eligible,
        hard_constraints=HardConstraintSet(
            must_do=j.constraints.must_do, avoid=j.constraints.avoid
        ),
        fairness=FairnessConfig(lambda_fairness=0.5, min_satisfaction_floor=0.3),
        event_thresholds=EventThresholds(),
    )
    print(
        "  Puede subir a:   "
        + "   ".join(
            f"{WHO[g.guest_id]} {len(j.eligible[g.guest_id])}/{len(j.catalog)}"
            for g in j.guests
        )
    )
    sofia_out = [
        j.name(a.node_id, 30)
        for a in j.catalog
        if a.height_restriction_cm and heights[SOFIA] < a.height_restriction_cm
    ]
    print(f"  Sofía no alcanza la estatura de: {', '.join(sorted(sofia_out))}")
    print("\n  Mejores opciones para el grupo (utilidad):")
    for nid, u in sorted(j.utilities.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
        riders = [WHO[g] for g in j.eligible if nid in j.eligible[g]]
        who = "todos" if len(riders) == len(j.guests) else ", ".join(riders)
        print(f"     {u:5.2f}  {j.name(nid):<44} suben: {who}")
    key(
        "el plan es para el grupo, no para una persona: cada atracción se valora por quienes pueden subir, "
        "y nadie queda incluido en algo que no puede hacer."
    )
    j.status["4 objetivo del grupo"] = "SUSTITUTO (P0-16 resolver, P0-17 scorer)"


# ---------------------------------------------------------------------------
# 5. PRONÓSTICO
# ---------------------------------------------------------------------------
def step5_forecast(j: Journey) -> None:
    header(
        "5. PRONÓSTICO DE FILAS -- la fila que habrá cuando la familia llegue, no la de ahora"
    )
    step(
        "ForecastService responde 'cuánta fila habrá en X a la hora T' con una cadena: pronóstico del API -> "
        "perfil histórico -> última foto reciente. Cada respuesta dice de dónde salió (procedencia).",
        "build_forecast_service sobre la misma foto; fila publicada ahora vs pronóstico para las 14:00.",
        "CONSTRUIDO (P0-18 ForecastService, PR #72 en revisión)",
        "PENDIENTE  que el optimizador lo use (issue de seguimiento redactado)",
    )
    try:
        from parkmind.services.planning.forecast_service import forecast_strategy_label
        from parkmind.services.use_cases.forecast import build_forecast_service
    except ImportError:
        print(
            "  omitido: esta rama no tiene ForecastService (correr desde feature/forecast-service)"
        )
        j.status["5 pronóstico"] = (
            "CONSTRUIDO en feature/forecast-service (PR #72), aún no en main"
        )
        return
    assert j.context is not None
    service = build_forecast_service(j.snapshots, j.id_mappings, now=j.now)
    at = j.now.replace(hour=14, minute=0, second=0, microsecond=0)
    source = {
        "api_forecast": "pronóstico del API",
        "historical_profile": "perfil histórico",
        "cached_snapshot": "última foto (fila actual)",
    }
    print(
        f"  Cadena, en orden: {' -> '.join(source.get(s, s) for s in service.strategy_names)}\n"
    )
    print(f"  {'atracción':<44} {'ahora':>6} {'a las ' + t(at):>11}   fuente")
    answers = []
    for nid, _u in sorted(j.utilities.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
        posted = j.context.waits.get(nid)
        f = service.forecast_wait(nid, at, now=j.now)
        if f:
            answers.append(f)
        now_w = f"{posted.wait_minutes:.0f}" if posted else "-"
        then_w = f"{f.wait_minutes:.0f}" if f else "-"
        print(
            f"  {j.name(nid):<44} {now_w:>6} {then_w:>11}   "
            f"{source.get(f.strategy, f.strategy) if f else 'sin dato (no tiene fila) -> decide quién llama'}"
        )
    print(
        f"\n  Un plan hecho con esto registraria en su procedencia: forecast_strategy = '{forecast_strategy_label(answers)}'"
    )
    rises = [
        (f.wait_minutes - j.context.waits[f.attraction_id].wait_minutes, f)
        for f in answers
        if f.attraction_id in j.context.waits
    ]
    delta, biggest = max(rises, key=lambda r: r[0]) if rises else (0.0, None)
    if biggest is not None and delta > 0:
        now_w = j.context.waits[biggest.attraction_id].wait_minutes
        key(
            f"{j.name(biggest.attraction_id)} tiene {now_w:.0f} min de fila ahora pero {biggest.wait_minutes:.0f} "
            f"a las {t(at)}: planear con la fila de ahora es planear mal. Y si no hay dato, el sistema no inventa uno."
        )
    else:
        key(
            "cada fila viene con su fuente; y si no hay dato, el sistema no inventa uno."
        )
    j.status["5 pronóstico"] = (
        "CONSTRUIDO (P0-18, PR #72); conexión al optimizador PENDIENTE"
    )


# ---------------------------------------------------------------------------
# 6. PLAN + 7. COMPUERTA
# ---------------------------------------------------------------------------
def _build(j: Journey, constraints: PartyConstraints) -> Plan:
    assert j.context is not None and j.park is not None
    graph = ParkGraph.from_sources(  # P0-13 (#71): catalogo + horario + estados en vivo + alias de zonas
        routing=RoutingClient(fallback_enabled=True),
        park=j.park,
        attractions=j.catalog,
        live_context=j.context,
        land_aliases=MAGIC_KINGDOM_LAND_ALIASES,
    )
    optimizer = GreedyInsertionOptimizer(park_graph=graph)
    return optimizer.build_plan(
        constraints,
        j.context,
        j.utilities,
        accessibility_reqs=j.accessibility,
        catalog=j.catalog,
        group_objective=j.objective,
    )


def _print_plan(j: Journey, plan: Plan) -> None:
    print(
        f"  {'llega':>6} {'sale':>6}  {'tipo':<10} {'parada':<44} {'fila':>4} {'cam.':>4}  quienes"
    )
    for s in plan.stops:
        riders = (
            "todos"
            if len(s.served_guests) in (0, len(j.guests))
            else ", ".join(WHO.get(g, g) for g in s.served_guests)
        )
        if s.kind == StopKind.REST:
            label = "(descanso)"
        elif s.kind == StopKind.MEAL:
            label = "(almuerzo; restaurante aún sin elegir)"
        else:
            label = j.name(s.node_id)
        print(
            f"  {t(s.arrival_time):>6} {t(s.departure_time):>6}  {KIND[s.kind.value]:<10} {label:<44} "
            f"{s.expected_wait_minutes:>4.0f} {s.walking_minutes:>4.0f}  {riders}"
        )
    unmet = [j.name(x) for x in plan.unmet_must_do]
    print(
        f"\n  Totales: {plan.total_wait_minutes:.0f} min de fila, {plan.total_walking_minutes:.0f} min caminando; "
        f"imprescindibles sin cubrir: {', '.join(unmet) or 'ninguno'}"
    )
    print(
        "  Satisfacción por persona: "
        + ", ".join(
            f"{WHO.get(g, g)} {v:.2f}" for g, v in plan.per_guest_satisfaction.items()
        )
    )


def _check(j: Journey, plan: Plan, constraints: PartyConstraints, context: LiveContext):  # type: ignore[no-untyped-def]
    assert j.park is not None
    return ConstraintChecker().check(
        plan,
        constraints,
        j.accessibility,
        {a.node_id: a for a in j.catalog},
        j.park,
        context,
        j.now,
    )


def _explain(j: Journey, v) -> str:  # type: ignore[no-untyped-def]
    """Una frase en español para las violaciones que aparecen en esta demo; el resto, el mensaje original."""
    where = j.name(v.stop_id) if v.stop_id else ""
    if v.rule.value == "SHOW_ARRIVAL":
        return f"el plan llega a {where} justo a la hora del show; la regla pide estar 5 min antes."
    if v.rule.value == "ACCESSIBILITY" and "rest frequency" in v.message:
        return f"Carmen pasaría más de 90 min sin descanso antes de {where}."
    if v.rule.value == "OPENING_HOURS" and "DOWN" in v.message:
        return f"{where} está caída: no puede quedar en el plan."
    return j.humanize(v.message)


def _hint(j: Journey, v) -> str:  # type: ignore[no-untyped-def]
    """La sugerencia de reparación de la compuerta, en español para los casos de esta demo."""
    where = j.name(v.stop_id) if v.stop_id else ""
    if v.rule.value == "SHOW_ARRIVAL":
        return f"mover {where} a una función programada, o quitarlo."
    if v.rule.value == "ACCESSIBILITY" and "REST" in (v.suggestion or ""):
        return f"insertar un DESCANSO para Carmen antes de {where}."
    if v.rule.value == "OPENING_HOURS":
        return f"prohibir {where} y volver a resolver."
    return j.humanize(v.suggestion or "")


def _print_check(j: Journey, result) -> None:  # type: ignore[no-untyped-def]
    n = len(result.violations)
    print(
        f"  Veredicto de la compuerta: {'VÁLIDO' if result.valid else 'RECHAZADO'} "
        f"({n} {'violaciones' if n != 1 else 'violación'})"
    )
    for v in result.violations:
        print(f"     Regla {RULE.get(v.rule.value, v.rule.value)} [{v.rule.value}]")
        say(f"-> {_explain(j, v)}", indent="        ")
        if v.suggestion:
            say(f"   reparación sugerida: {_hint(j, v)}", indent="        ")


REST_MINUTES = 20

_FINDINGS = {
    "SHOW_ARRIVAL": "Regla 3 vs optimizador: el optimizador pone la llegada a un show EXACTAMENTE a la hora del show; "
    "el checker exige llegar 5 min antes (constraint_checker.py _check_show_arrival). Tal como está, "
    "todo show imprescindible queda rechazado.",
    "ACCESSIBILITY": "Regla 9 (descansos) vs optimizador: el optimizador cuenta la comida como descanso "
    "(optimizer.py:271/456); el checker solo cuenta paradas REST (constraint_checker.py:423-431). "
    "Decisión de equipo: ¿una comida sentada cuenta como descanso?",
}


def _insert_rest_before(
    j: Journey, plan: Plan, stop_id: str, departure: datetime
) -> Plan:
    """Sustituto del movimiento de reparacion 'insertar DESCANSO' (P0-21): la sugerencia de la propia
    compuerta, aplicada al pie de la letra. El descanso toma el inicio de la parada senalada; esa
    parada y las siguientes se corren REST_MINUTES, y se descarta lo que termine despues de la salida.
    """
    idx = next(i for i, s in enumerate(plan.stops) if s.node_id == stop_id)
    shift = timedelta(minutes=REST_MINUTES)
    flagged = plan.stops[idx]
    where = plan.stops[idx - 1].node_id if idx else flagged.node_id
    rest = flagged.model_copy(
        update={
            "node_id": where,
            "kind": StopKind.REST,
            "departure_time": flagged.arrival_time + shift,
            "expected_wait_minutes": 0.0,
            "walking_minutes": 0.0,
            "utility": 0.0,
            "served_guests": [g.guest_id for g in j.guests],
        }
    )
    later = [
        s.model_copy(
            update={
                "arrival_time": s.arrival_time + shift,
                "departure_time": s.departure_time + shift,
            }
        )
        for s in plan.stops[idx:]
    ]
    stops = [
        *plan.stops[:idx],
        rest,
        *[s for s in later if s.departure_time <= departure],
    ]
    return plan.model_copy(update={"stops": stops})


def step6_7_plan_and_check(j: Journey) -> None:
    header("6. EL PLAN -- inserción voraz sobre caminatas, filas y ventanas fijas")
    step(
        "el optimizador fija primero lo que tiene hora (imprescindibles, show, almuerzo), luego llena el día "
        "por utilidad por minuto, con caminatas reales del grafo del parque y descansos según Carmen.",
        "el GreedyInsertionOptimizer real + ParkGraph + RoutingClient (distancias sobre coordenadas curadas).",
        "CONSTRUIDO (P0-19 optimizador, P0-09 rutas, P0-13 ParkGraph)",
    )
    assert j.constraints is not None and j.context is not None
    if j.constraints.departure_time <= j.now + timedelta(minutes=30):
        say(
            f"El parque cierra a las {t(j.constraints.departure_time)} y son las {t(j.now)}: ya no queda día "
            "que planear. Use --live en horario del parque, o la captura del 2026-09-27 (por defecto)."
        )
        j.status["6 plan"] = "CONSTRUIDO (P0-19); nada que planear (parque cerrado)"
        return
    plan = _build(j, j.constraints)
    print("  Primer candidato:")
    _print_plan(j, plan)
    key(
        "ningún LLM ordena las paradas: el orden lo decide un algoritmo determinista, "
        "y la misma entrada da siempre el mismo plan."
    )
    pause()

    header(
        "7. LA COMPUERTA -- 11 reglas duras; nada llega a la familia sin pasar por aquí"
    )
    step(
        "ConstraintChecker valida el candidato contra cada regla dura; si falla, el plan vuelve al optimizador "
        "con un movimiento de reparación con nombre (re-resolver acotado, sección 21).",
        "el ConstraintChecker real sobre el plan de arriba.",
        "CONSTRUIDO (P0-15/P0-20 ConstraintChecker, las 11 reglas)",
        "PENDIENTE  (P0-21 movimientos de reparación): aquí se reparan a mano, marcados como SUSTITUTO",
    )
    constraints = j.constraints
    result = _check(j, plan, constraints, j.context)
    _print_check(j, result)
    for rule in sorted({v.rule.value for v in result.violations}):
        if rule in _FINDINGS:
            j.findings.append(_FINDINGS[rule])

    if any(v.rule.value == "SHOW_ARRIVAL" for v in result.violations):
        print()
        say(
            "Reparación 1 (SUSTITUTO de P0-21 'quitar la parada con hora'): este optimizador no logra llegar al "
            "desfile 5 min antes, así que el desfile sale de los imprescindibles y se vuelve a resolver el día."
        )
        constraints = constraints.model_copy(
            update={
                "must_do": [
                    m
                    for m in constraints.must_do
                    if m not in MAGIC_KINGDOM_SCHEDULED_SHOWS
                ]
            }
        )
        plan = _build(j, constraints)
        result = _check(j, plan, constraints, j.context)
        _print_check(j, result)

    for attempt in range(2, 5):
        rests = [
            v
            for v in result.violations
            if v.rule.value == "ACCESSIBILITY"
            and v.stop_id
            and "rest frequency" in v.message
        ]
        if result.valid or not rests or len(rests) != len(result.violations):
            break
        print()
        say(
            f"Reparación {attempt} (SUSTITUTO de P0-21 'insertar DESCANSO'): se aplica la sugerencia de la propia "
            f"compuerta -- un descanso de {REST_MINUTES} min antes de {j.name(rests[0].stop_id)}; lo demás se corre."
        )  # type: ignore[arg-type]
        plan = _insert_rest_before(
            j, plan, rests[0].stop_id, constraints.departure_time
        )  # type: ignore[arg-type]
        result = _check(j, plan, constraints, j.context)
        _print_check(j, result)

    print("\n  Plan final:")
    _print_plan(j, plan)
    print()
    _print_check(j, result)
    key(
        "la compuerta encontró dos errores reales que los tests por separado no veían. Esa es su razón de "
        "ser: el plan que ve la familia ya paso las 11 reglas."
    )
    j.constraints = constraints
    j.plan, j.plan_valid = plan, result.valid
    j.status["6 plan"] = "CONSTRUIDO (P0-19)"
    j.status["7 compuerta"] = "CONSTRUIDO (P0-15/20); reparaciones PENDIENTES (P0-21)"


# ---------------------------------------------------------------------------
# 8. PROPONER -> APROBAR -> GUARDAR (Postgres, opcional)
# ---------------------------------------------------------------------------
def step8_persist(j: Journey, enabled: bool) -> None:
    header(
        "8. APROBACIÓN Y PRIVACIDAD -- la familia decide, la base de datos recuerda (lo justo)"
    )
    step(
        "el grafo se detiene en un interrupt() con una propuesta PENDIENTE; la familia aprueba en la app; "
        "el plan pasa a ACTIVO. Las necesidades de accesibilidad viven solo en la sesión.",
        "una base Postgres desechable: migrar, guardar plan + propuesta PENDIENTE, aprobar, activar, y comprobar "
        "que lo de Carmen nunca llegó a una tabla. La base se borra al final.",
        "CONSTRUIDO (P0-12 repositorios + session store, P0-28 grafo/interrupt; aquí no se corre el grafo)",
    )
    if not enabled:
        print("  omitido (--no-db)")
        j.status["8 aprobación"] = "CONSTRUIDO (P0-12/28); omitido en esta corrida"
        return
    if j.plan is None:
        print("  omitido: no hay plan del paso 6")
        return
    if not j.plan_valid:
        say(
            "NOTA: la compuerta rechazó este plan. En producción nunca se propone un plan rechazado; aqui se "
            "guarda solo para mostrar el mecanismo."
        )
    try:
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        from parkmind.config.settings import settings
        from parkmind.services.clients.postgres import (
            PostgresGuestRepository,
            PostgresPlanRepository,
            PostgresProposalRepository,
            PostgresSessionStore,
            SessionMemory,
            migrate,
        )

        def url_for(database: str) -> str:
            return (
                make_url(settings.DATABASE_URL)
                .set(database=database)
                .render_as_string(hide_password=False)
            )

        admin_url = url_for("postgres")
        name = f"parkmind_demo_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except Exception as exc:  # noqa: BLE001 -- demo: Postgres es opcional
        print(
            f"  omitido: Postgres no responde ({type(exc).__name__}). Levantelo con `docker compose up -d`."
        )
        j.status["8 aprobación"] = "CONSTRUIDO (P0-12/28); omitido (sin Postgres)"
        return
    from factories import proposal as proposal_factory

    url = url_for(name)
    try:
        migrate.upgrade(url)
        print(f"  Base desechable {name} creada y migrada")
        with psycopg.connect(url) as conn:
            for g in j.guests:
                PostgresGuestRepository(conn).save(g)
            plans, proposals = (
                PostgresPlanRepository(conn),
                PostgresProposalRepository(conn),
            )
            plans.save(THREAD, j.plan)
            prop = proposal_factory(
                # primer plan del día: no hay plan anterior, asi que base = candidato (como ProposePlanUseCase)
                proposal_id=f"prop_{j.plan.plan_id[:8]}",
                base_plan_id=j.plan.plan_id,
                candidate_plan_id=j.plan.plan_id,
                reason="initial plan",
                explanation="Cubre Peter Pan, almuerza en su ventana y respeta los descansos de Carmen.",
                provenance=j.plan.provenance,
            )
            proposals.save(THREAD, prop)
            stored = proposals.get(prop.proposal_id)
            print(
                f"  Plan guardado y propuesta creada: estado {stored.approval_status.value if stored else '?'}"
            )
            print("  ... la familia toca 'Aprobar' en la app ...")
            proposals.resolve(prop.proposal_id, ApprovalStatus.APPROVED, at=j.now)
            plans.activate(THREAD, j.plan.plan_id, at=j.now)
            active = plans.get_active(THREAD)
            print(
                f"  Propuesta APPROVED -> plan ACTIVO del hilo: {len(active.stops) if active else 0} paradas"
            )

            memory = SessionMemory()
            store = PostgresSessionStore(conn, memory)
            for req in j.accessibility:
                store.put(THREAD, req)
            held = store.get(THREAD, CARMEN)
            print(
                f"\n  Necesidades de Carmen en la sesión: {[m.value for m in held.mobility_requirements] if held else None}"
            )
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                ).fetchall()
            ]
            hits = 0
            for table in tables:
                q = sql.SQL(
                    "SELECT count(*) FROM {} AS r WHERE r::text LIKE %s"
                ).format(sql.Identifier(table))
                row = conn.execute(q, ("%WHEELCHAIR%",)).fetchone()
                hits += row[0] if row else 0
            print(
                f"  Filas que mencionan WHEELCHAIR en las {len(tables)} tablas de la base: {hits}"
            )
            store.end_session(THREAD)
            print(f"  Al terminar la sesión: {store.get(THREAD, CARMEN)}")
        key(
            "solo un humano activa un plan, y los datos de salud de Carmen se usan para planear pero nunca "
            "se guardan en disco (privacidad por diseño, C19)."
        )
        j.status["8 aprobación"] = "CONSTRUIDO (P0-12/28)"
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )
        print(f"\n  Base {name} borrada")


# ---------------------------------------------------------------------------
# 9. UN EVENTO DURANTE EL DIA
# ---------------------------------------------------------------------------
def step9_event(j: Journey) -> None:
    header(
        "9. DURANTE EL DÍA -- una atracción se cae: por qué el plan tiene que cambiar"
    )
    step(
        "el monitor de fotos ve que la próxima atracción se cayó, la EventPolicy lo califica y el replanificador "
        "propone un plan nuevo con sus diferencias para que la familia lo apruebe.",
        "se marca como caída la primera atracción del plan aprobado y se vuelve a pasar por la compuerta.",
        "CONSTRUIDO (P0-20 regla 1, horario y estado)",
        "PENDIENTE  (P0-21 replanificador / P0-22 diff de planes -- PR #70 en revisión; P0-24 monitor)",
    )
    if j.plan is None or j.context is None or j.constraints is None:
        print("  omitido: no hay plan")
        return
    nxt = next((s for s in j.plan.stops if s.kind == StopKind.ATTRACTION), None)
    if nxt is None:
        print("  omitido: el plan no tiene atracciones")
        return
    print(
        f"  Evento: ATTRACTION_DOWN -- {j.name(nxt.node_id)} (planeada a las {t(nxt.arrival_time)})"
    )
    statuses = {**j.context.statuses, nxt.node_id: AttractionStatus.DOWN}
    waits = dict(j.context.waits)
    if nxt.node_id in waits:
        waits[nxt.node_id] = waits[nxt.node_id].model_copy(
            update={"status": AttractionStatus.DOWN}
        )
    down = j.context.model_copy(update={"statuses": statuses, "waits": waits})
    _print_check(j, _check(j, j.plan, j.constraints, down))
    key(
        "el mismo juez que aprobó el plan detecta que dejó de ser válido. Lo que falta es el replanificador "
        "que proponga el cambio; el disparador ya funciona."
    )
    j.status["9 replanificar"] = (
        "disparador CONSTRUIDO (regla 1); replanificador PENDIENTE (P0-21/22)"
    )


# ---------------------------------------------------------------------------
def wrap_up(j: Journey) -> None:
    header("10. DÓNDE ESTAMOS")
    for name_, status in j.status.items():
        print(f"  {name_:<22} {status}")
    print(f"  {'explicación':<22} PENDIENTE (P0-29 explicación del Concierge)")
    print(f"  {'aprender del uso':<22} PENDIENTE (P0-33 BehaviorLog / aprendizaje)")
    if j.findings:
        print("\n  Hallazgos de esta corrida (para el equipo):")
        for f in j.findings:
            say(f"- {f}", indent="   ")
    key(
        "el núcleo determinista ya existe y está probado de punta a punta con datos reales; lo que falta "
        "es la capa conversacional (LLM) y la reparación/replanificación automática."
    )


def main() -> int:
    global PAUSE
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="datos reales de hoy (requiere red y horario del parque)",
    )
    parser.add_argument(
        "--no-db", action="store_true", help="omitir la sección de Postgres"
    )
    parser.add_argument(
        "--pausa",
        action="store_true",
        help="esperar Enter entre pasos (para presentar)",
    )
    args = parser.parse_args()
    PAUSE = args.pausa
    j = Journey(live=args.live)
    load_park(j)
    intro(j)
    failures = 0
    steps = [
        step1_elicit,
        step2_profiles,
        step3_context,
        step4_objective,
        step5_forecast,
        step6_7_plan_and_check,
        lambda jj: step8_persist(jj, not args.no_db),
        step9_event,
    ]
    for fn in steps:
        try:
            fn(j)
        except Exception as exc:  # noqa: BLE001 -- demo: reportar y seguir
            failures += 1
            print(
                f"\n  !! {getattr(fn, '__name__', 'paso')} falló: {type(exc).__name__}: {exc}"
            )
        pause()
    wrap_up(j)
    print("\nFin." if not failures else f"\nFin, con {failures} paso(s) fallido(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
