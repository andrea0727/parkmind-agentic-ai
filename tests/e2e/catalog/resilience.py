"""Scenarios of the area: resilience."""

from e2e.catalog._common import H, adult, extraction
from e2e.kit.verdicts import j_outage, j_snapshot
from e2e.model import Criteria, Fault, Offline, Scenario

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="proveedor_caido_con_snapshot",
        number=16,
        area="Resilience: outages",
        severity="critical",
        title="ThemeParks is down, but the catalog is already stored",
        what="Resilience: with the live provider unavailable, the app should fall back to the stored catalog.",
        steps=(
            "Simulate every ThemeParks call failing",
            "Guests (2 adults) want Dumbo, leave at 10 PM",
            "Guests confirm",
            "The app builds a plan from stored data",
        ),
        setup="Every ThemeParks call raises ThemeParksUnavailableError; Postgres has the catalog",
        messages=(("We are 2 adults. " + H + "We want Dumbo. We leave at 10 PM."),),
        expected="A plan is produced from the stored data.",
        judge=j_snapshot,
        why="",
        criteria=Criteria(
            workflow="With the collector down, the stored snapshot is used and the run reaches propose",
            llm_eval="",
            observability="The failed provider call is visible in the trace and the run still ends without an ERROR root",
            business_rules="Stale data is used only when it is inside the freshness limit",
        ),
        fault=Fault.PROVIDER_DOWN,
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)], must_do=["Teacups"], departure="22:00"
            ),
            down=("provider",),
        ),
    ),
    Scenario(
        id="proveedor_caido_db_vacia",
        number=17,
        area="Resilience: outages",
        severity="critical",
        title="ThemeParks is down and the database has no catalog",
        what="Resilience: with nothing to fall back to, the guests must get a clear message, not a crash.",
        steps=(
            "Simulate ThemeParks failing and use an empty database",
            "Guests (2 adults) want Dumbo, leave at 10 PM",
            "Guests confirm",
            "The app tries to load the catalog",
        ),
        setup="ThemeParks down, throwaway database with no catalog",
        messages=(("We are 2 adults. " + H + "We want Dumbo. We leave at 10 PM."),),
        expected="The guests receive a clear 'service unavailable' message.",
        judge=j_outage("A provider outage with an empty database"),
        why="planning_deps.open_deps wraps the failure in PlanningUnavailableError and load_catalog raises CatalogUnavailableError; no graph node turns these typed errors into a message.",
        criteria=Criteria(
            workflow="With no collector and no stored data the run ends with a typed unavailability, not a crash",
            llm_eval="",
            observability="The failing node has an ERROR span carrying the error type only",
            business_rules="The guests get a service-unavailable message instead of a stack trace",
        ),
        fault=Fault.PROVIDER_DOWN_EMPTY_DB,
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)], must_do=["Teacups"], departure="22:00"
            ),
            down=("provider", "empty_store"),
        ),
    ),
    Scenario(
        id="db_caida",
        number=18,
        area="Resilience: outages",
        severity="critical",
        title="The database is down",
        what="Resilience: a database outage must be reported to the guests, not leak as a raw error.",
        steps=(
            "Point the app at a database port nobody listens on",
            "Guests (2 adults) want Dumbo, leave at 10 PM",
            "Guests confirm",
        ),
        setup="DATABASE_URL points at a closed port",
        messages=(("We are 2 adults. " + H + "We want Dumbo. We leave at 10 PM."),),
        expected="The guests receive a clear failure message.",
        judge=j_outage("A database outage"),
        why="psycopg's OperationalError propagates out of the graph untouched.",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="The failing node has an ERROR span carrying the error type only",
            business_rules="The guests get a failure message; connection strings never reach them",
        ),
        fault=Fault.DB_DOWN,
    ),
)
