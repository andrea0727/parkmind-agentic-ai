"""Scenarios of the area: happy path."""

from e2e.catalog._common import HAPPY, adult, child, extraction
from e2e.kit.verdicts import j_happy, j_plan_active, j_silla
from e2e.model import Criteria, Fault, Offline, Scenario

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="happy_catalogo_vacio",
        number=1,
        area="Happy path",
        severity="blocker",
        title="Full happy path on a brand-new database (empty catalog)",
        what="The complete journey when the app has never stored the park catalog: from the guests' first message to an approved plan.",
        steps=(
            "Start with an empty catalog database",
            "Guests describe 2 adults + 2 kids, want TRON and Space Mountain, leave at 10 PM, lunch around 1 PM",
            "The app loads the park catalog from ThemeParks (once)",
            "Guests confirm the extracted constraints",
            "The app builds, checks and explains a plan",
            "Guests approve the plan",
        ),
        setup="Postgres catalog empty; ThemeParks, Open-Meteo and the LLM are live",
        messages=HAPPY,
        expected="A valid plan is proposed and it only becomes active after the guests approve it.",
        judge=j_happy(True),
        why="",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="The catalog load appears as a load_context span and the run is one trace",
            business_rules="The catalog is read from ThemeParks only when the database has none",
        ),
        fault=Fault.EMPTY_CATALOG_DB,
    ),
    Scenario(
        id="happy_bd_poblada",
        number=2,
        area="Happy path",
        severity="blocker",
        title="Full happy path with the catalog already stored",
        what="Same journey as scenario 1, but the park catalog is already in the database, so it must be read from there.",
        steps=(
            "Start with a populated catalog database",
            "Guests describe 2 adults + 2 kids, want TRON and Space Mountain, leave at 10 PM, lunch around 1 PM",
            "Guests confirm the extracted constraints",
            "The app builds, checks and explains a plan",
            "Guests approve the plan",
        ),
        setup="Postgres catalog already populated; live providers",
        messages=HAPPY,
        expected="A valid plan is proposed from stored data and it only becomes active after the guests approve it.",
        judge=j_happy(),
        why="",
        criteria=Criteria(
            workflow="Nodes run in order elicit, confirm, load_context, resolve_group, build_plan, check, explain, propose, approval; the plan is active only after APPROVED",
            llm_eval="The explanation is grounded in the plan: every stop it names is in the plan and every wait it quotes matches",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error; the elicit and explain nodes carry a generation span with model, tokens and latency",
            business_rules="The checker passes: opening hours, lunch window, walking budget, heights",
        ),
        offline=Offline(
            extraction=extraction(
                [
                    adult(175),
                    adult(168),
                    child(130, intensity="LOW"),
                    child(125, intensity="LOW"),
                ],
                must_do=["TRON", "Space Mountain"],
                departure="22:00",
                lunch=("12:30", "13:30"),
            )
        ),
    ),
    Scenario(
        id="silla_de_ruedas",
        number=11,
        area="Happy path",
        severity="critical",
        title="Party includes a wheelchair user",
        what="Accessibility: a party with a wheelchair user and a consent to use that information must still get a plan.",
        steps=(
            "Guests (2 adults + 1 child, one adult uses a wheelchair) want Haunted Mansion and Dumbo, leave at 10 PM",
            "Guests confirm, including consent for accessibility data",
            "The app builds a plan",
        ),
        setup="One adult uses a wheelchair, consent given",
        messages=(
            (
                "We are 2 adults and a child. One adult uses a wheelchair. We want Haunted Mansion and Dumbo. "
                "Adults are 170 cm and 165 cm, child is 120 cm. We leave at 10 PM."
            ),
        ),
        expected="A valid plan is produced.",
        judge=j_silla,
        why="",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="Accessibility needs are used only with the guests' consent and are never traced",
        ),
    ),
    Scenario(
        id="grupo_de_1",
        number=20,
        area="Happy path",
        severity="normal",
        title="Solo visitor (party of one)",
        what="Smallest valid party: one adult must get a normal plan.",
        steps=(
            "One adult (170 cm) wants Space Mountain, leaves at 10 PM",
            "Guests confirm",
            "The app builds a plan",
        ),
        setup="One adult, Space Mountain, 170 cm",
        messages=(
            ("Just me, one adult, 170 cm. I want Space Mountain. I leave at 10 PM."),
        ),
        expected="A valid plan is produced.",
        judge=j_plan_active(),
        why="",
        criteria=Criteria(
            workflow="A party of one reaches propose and approval",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A single guest is a valid party",
        ),
        offline=Offline(
            extraction=extraction(
                [adult(170)], must_do=["Space Mountain"], departure="22:00"
            )
        ),
    ),
)
