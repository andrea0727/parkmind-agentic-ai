"""Scenarios of the area: replanning after an attraction closes during the day."""

from e2e.catalog._common import HAPPY, adult, child, extraction
from e2e.kit.verdicts import j_closed_alternative, j_closed_rejected
from e2e.model import Criteria, FollowUp, Offline, Scenario

_PARTY = extraction(
    [adult(175), adult(168), child(130, intensity="LOW"), child(125, intensity="LOW")],
    must_do=["TRON", "Space Mountain"],
    departure="22:00",
    lunch=("12:30", "13:30"),
)

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="opcion_cerrada_alternativa",
        number=34,
        area="Replanning",
        severity="blocker",
        title="A planned ride closes and the guests get a valid alternative",
        what="After the plan is approved, one of its rides closes. The app must propose a new plan without that ride, and nothing changes until the guests approve it.",
        steps=(
            "Guests describe the party and approve a plan",
            "A monitor event reports that the first ride of the plan is down",
            "The replanning graph builds and checks an alternative",
            "The active plan is unchanged until the guests approve the alternative",
            "Guests approve the alternative",
        ),
        setup="An approved active plan; an ATTRACTION_DOWN event for its first ride",
        messages=HAPPY,
        expected="An alternative that avoids the closed ride passes the checker, is proposed, and replaces the active plan only after APPROVED.",
        judge=j_closed_alternative,
        criteria=Criteria(
            workflow="The event routes to the replanning graph; the proposal is checked before it is offered; resolving it replaces the active plan once",
            llm_eval="The replanning message names the closed ride and what changes, and invents nothing",
            observability="The replanning run is its own trace under the same session id as the initial plan",
            business_rules="A closed attraction is never in a proposed plan; the active plan changes only through an explicit APPROVED",
        ),
        follow_up=FollowUp("attraction_down", stop_index=0),
        offline=Offline(extraction=_PARTY),
    ),
    Scenario(
        id="opcion_cerrada_alternativa_rechazada",
        number=35,
        area="Replanning",
        severity="critical",
        title="The guests reject the alternative after a closure",
        what="The proposed alternative is turned down. The plan the guests approved earlier must remain the active one.",
        steps=(
            "Guests describe the party and approve a plan",
            "A monitor event reports that the first ride of the plan is down",
            "The app proposes an alternative",
            "Guests reject the alternative",
        ),
        setup="An approved active plan; an ATTRACTION_DOWN event for its first ride; the replan decision is REJECTED",
        messages=HAPPY,
        expected="The alternative is not activated and the previously approved plan stays active.",
        judge=j_closed_rejected,
        criteria=Criteria(
            workflow="A rejected proposal ends the replanning run without touching the active plan",
            observability="The rejection is visible in the trace and is not marked as an error",
            business_rules="No plan becomes active without an explicit APPROVED",
        ),
        follow_up=FollowUp(
            "attraction_down",
            stop_index=0,
            decision={"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"},
        ),
        offline=Offline(extraction=_PARTY),
    ),
)
