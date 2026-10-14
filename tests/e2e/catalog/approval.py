"""Scenarios of the area: approval."""

from e2e.catalog._common import H, adult, extraction
from e2e.kit.verdicts import j_garbage, j_no_planning, j_rechazo
from e2e.model import Criteria, Offline, Scenario

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="rechazo",
        number=6,
        area="Approval and consent",
        severity="critical",
        title="Guests reject the proposed plan",
        what="Approval: a plan must never become active if the guests say no.",
        steps=(
            "Guests (2 adults) want Haunted Mansion and Pirates, leave at 10 PM",
            "The app proposes a plan",
            "Guests answer REJECTED (too much walking)",
        ),
        setup="Approval resume is REJECTED / TOO_MUCH_WALKING",
        messages=(
            (
                "We are 2 adults. We want Haunted Mansion and Pirates of the Caribbean. Both adults are 170 cm. We leave at 10 PM."
            ),
        ),
        expected="No plan becomes active.",
        judge=j_rechazo,
        why="",
        criteria=Criteria(
            workflow="A REJECTED approval ends the run with no active plan and the rejection reason recorded",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A plan becomes active only through an explicit APPROVED",
        ),
        approval={"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"},
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)],
                must_do=["Haunted Mansion", "Big Thunder Mountain"],
                departure="22:00",
            )
        ),
    ),
    Scenario(
        id="confirmacion_rechazada",
        number=13,
        area="Approval and consent",
        severity="critical",
        title="Guests say the extracted constraints are wrong",
        what="Confirmation: if the guests do not confirm the summary, nothing may be planned.",
        steps=(
            "Guests (2 adults) want Dumbo, leave at 10 PM",
            "The app shows the extracted constraints",
            "Guests answer 'not confirmed'",
        ),
        setup="Confirmation resume is confirmed=false",
        messages=(("We are 2 adults. " + H + "We want Dumbo. We leave at 10 PM."),),
        expected="No planning runs and nothing is activated; the app asks what to change.",
        judge=j_no_planning,
        why="",
        criteria=Criteria(
            workflow="A confirmation with confirmed=false ends before load_context; nothing is activated",
            llm_eval="",
            observability="The trace stops after the elicit node: no planning span exists and the pause is not marked as an error",
            business_rules="Nothing is planned from constraints the guests did not confirm",
        ),
        confirm={"confirmed": False, "consent": True},
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)], must_do=["Teacups"], departure="22:00"
            )
        ),
    ),
    Scenario(
        id="confirmacion_basura",
        number=14,
        area="Approval and consent",
        severity="normal",
        title="Confirmation answer is malformed (the text 'yes' instead of a structured answer)",
        what="Robustness: an answer in an unexpected format must not crash the graph.",
        steps=(
            "Guests (2 adults) want Dumbo, leave at 10 PM",
            "The app shows the extracted constraints",
            "The confirmation answer arrives as the plain string 'yes'",
        ),
        setup="Confirmation resume is the string 'yes'",
        messages=(("We are 2 adults. " + H + "We want Dumbo. We leave at 10 PM."),),
        expected="The app fails safely (asks again) and does not crash.",
        judge=j_garbage,
        why="The confirmation node reads the resume value as a mapping and nothing validates its type at the boundary.",
        criteria=Criteria(
            workflow="A malformed confirmation payload re-asks or stops safely and never raises",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="Input at the interrupt boundary is validated, not trusted",
        ),
        confirm="yes",
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)], must_do=["Teacups"], departure="22:00"
            )
        ),
    ),
    Scenario(
        id="sin_consentimiento_accesibilidad",
        number=15,
        area="Approval and consent",
        severity="critical",
        title="Wheelchair user does not consent to using accessibility data",
        what="Privacy and consent: without consent, accessibility information must not be used for planning.",
        steps=(
            "Guests (2 adults, one uses a wheelchair) want Dumbo, leave at 10 PM",
            "The app asks for confirmation",
            "Guests confirm but answer consent = false",
        ),
        setup="Wheelchair user, consent=false",
        messages=(
            (
                "We are 2 adults, one uses a wheelchair. "
                + H
                + "We want Dumbo. We leave at 10 PM."
            ),
        ),
        expected="No planning runs and nothing is activated.",
        judge=j_no_planning,
        why="",
        criteria=Criteria(
            workflow="Without consent the graph ends before load_context and no accessibility data is stored",
            llm_eval="",
            observability="The trace stops after the elicit node: no planning span exists and the pause is not marked as an error",
            business_rules="Accessibility data is processed only with consent",
        ),
        confirm={"confirmed": True, "consent": False},
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)],
                must_do=["Teacups"],
                departure="22:00",
                wheelchair=2,
            )
        ),
    ),
)
