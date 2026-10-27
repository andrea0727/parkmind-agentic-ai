"""Scenarios of the area: safety."""

from e2e.kit.verdicts import (
    j_altura,
    j_nino,
    j_sin_alturas_con_mustdo,
    j_sin_alturas_sin_mustdo,
)
from e2e.model import Criteria, Scenario

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="sin_alturas_sin_mustdo",
        number=3,
        area="Safety: heights",
        severity="critical",
        title="Heights not given, but the wanted rides have no height limit",
        what="Safety: when nobody tells us the heights, rides with a minimum height must never be put in the plan.",
        steps=(
            "Guests (2 adults + 2 kids) want Dumbo and Haunted Mansion and leave at 10 PM, without giving any height",
            "Guests confirm the constraints",
            "The app builds a plan",
        ),
        setup="Heights not mentioned",
        messages=(
            (
                "We are 2 adults and 2 kids. We want to ride Dumbo and Haunted Mansion. We leave at 10 PM."
            ),
        ),
        expected="A plan is made and it contains no ride that has a minimum height.",
        judge=j_sin_alturas_sin_mustdo,
        why="",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A guest with an unknown height is never put on a ride with a minimum height (fail closed)",
        ),
    ),
    Scenario(
        id="sin_alturas_con_mustdo",
        number=4,
        area="Safety: heights",
        severity="critical",
        title="Heights not given, but a wanted ride has a minimum height",
        what="Safety: Space Mountain needs 112 cm and we do not know the guests' heights. The app should ask for the heights or plan without that ride.",
        steps=(
            "Guests (2 adults + 2 kids) say they definitely want Space Mountain and leave at 10 PM, without giving any height",
            "Look at what the app does next",
        ),
        setup="Heights not mentioned, Space Mountain (112 cm) is a must-do",
        messages=(
            (
                "We are 2 adults and 2 kids. We definitely want Space Mountain. We leave at 10 PM."
            ),
        ),
        expected="The app asks for the guests' heights because Space Mountain has a minimum height.",
        judge=j_sin_alturas_con_mustdo,
        why="ELICIT asks once; the checker still fails closed for a guest who never answers.",
        criteria=Criteria(
            workflow="",
            llm_eval="The question names the missing heights and is not an invented requirement",
            observability="The trace stops after the elicit node: no planning span exists and the pause is not marked as an error",
            business_rules="A must-do with a minimum height and unknown heights asks once; the checker still fails closed",
        ),
    ),
    Scenario(
        id="nino_muy_bajo",
        number=5,
        area="Safety: heights",
        severity="critical",
        title="A 90 cm child in a party that wants Space Mountain (112 cm minimum)",
        what="Safety: a child below the minimum height must not be assigned to the ride, and the explanation must say so.",
        steps=(
            "Guests (2 adults 175/168 cm + 1 child 90 cm) say they definitely want Space Mountain and leave at 10 PM",
            "Guests confirm the constraints",
            "The app builds a plan and explains it",
        ),
        setup="Child 90 cm, adults 175 and 168 cm",
        messages=(
            (
                "We are 2 adults and 1 child. We definitely want Space Mountain. The adults are 175 cm and 168 cm, "
                "the child is 90 cm. We leave at 10 PM."
            ),
        ),
        expected="The child does not board Space Mountain, and the explanation tells the guests that.",
        judge=j_nino,
        why="The explanation template only lists stops, waits and walking; it carries no per-guest eligibility information.",
        criteria=Criteria(
            workflow="",
            llm_eval="The explanation does not claim the child rides Space Mountain",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A guest below the minimum height never boards the ride",
        ),
    ),
    Scenario(
        id="altura_absurda",
        number=29,
        area="Safety: heights",
        severity="critical",
        title="Impossible heights (400 cm and 20 cm)",
        what="Data integrity: implausible values must be validated or rejected, never silently changed.",
        steps=(
            "Guests say one adult is 400 cm and the other 20 cm, want Dumbo, leave at 10 PM",
            "Compare the extracted heights with what the guests wrote",
        ),
        setup="'One is 400 cm and the other 20 cm'",
        messages=(
            (
                "We are 2 adults, one is 400 cm and the other 20 cm tall. We want Dumbo. We leave at 10 PM."
            ),
        ),
        expected="The values are rejected or questioned, and are never silently replaced.",
        judge=j_altura,
        why="The extractor output replaced the values, so the original numbers never reached schema validation.",
        criteria=Criteria(
            workflow="",
            llm_eval="The extractor copies the stated heights verbatim and never normalizes them",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="Heights are validated against the schema, never silently changed",
        ),
    ),
)
