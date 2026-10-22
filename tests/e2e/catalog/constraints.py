"""Scenarios of the area: constraints."""

from e2e.catalog._common import H, adult, extraction, soon
from e2e.kit.verdicts import (
    j_asks,
    j_avoid,
    j_mustdos,
    j_past,
    j_show,
    j_stops_at_check,
)
from e2e.model import Criteria, Offline, Scenario

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="salida_ya_pasada",
        number=12,
        area="Constraints and feasibility",
        severity="critical",
        title="Guests say they leave at a time that has already passed",
        what="Feasibility: the app should not activate a plan that lies in the past.",
        steps=(
            "Guests (2 adults, 170 cm) want Haunted Mansion and say they leave at 9 AM while the park time is later",
            "Look at what the app does next",
        ),
        setup="Departure 9 AM while the park time is evening",
        messages=(
            (
                "We are 2 adults, both 170 cm. We want Haunted Mansion. We leave at 9 AM."
            ),
        ),
        expected="The app refuses or asks; it does not activate a plan that starts in the past.",
        judge=j_past,
        why="The optimizer starts the day at park.opening_time (08:05) and the checker has no 'plan is in the past' rule (the max(opening, now) item).",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A plan never starts in the past: the day starts at the later of opening and now",
        ),
    ),
    Scenario(
        id="doce_mustdo",
        number=25,
        area="Constraints and feasibility",
        severity="normal",
        title="Guests ask for 12 must-do rides",
        what="Feasibility: too many must-dos for one day should be planned or reported as not met.",
        steps=(
            "Guests (2 adults) list 12 major rides as must-do, leave at 10 PM",
            "Guests confirm",
            "The app builds a plan",
        ),
        setup="Twelve major rides as must-do",
        messages=(
            (
                "We are 2 adults. "
                + H
                + "Must do: Space Mountain, TRON, Big Thunder Mountain, Seven Dwarfs Mine Train, "
                "Haunted Mansion, Pirates of the Caribbean, Jungle Cruise, Peter Pan's Flight, Dumbo, Barnstormer, "
                "Buzz Lightyear, Tiana's Bayou Adventure. We leave at 10 PM."
            ),
        ),
        expected="Every must-do is in the plan, or the missing ones are reported.",
        judge=j_mustdos(
            [
                "Space Mountain",
                "TRON",
                "Big Thunder",
                "Seven Dwarfs",
                "Haunted Mansion",
                "Pirates",
                "Jungle Cruise",
                "Peter Pan",
                "Dumbo",
                "Barnstormer",
                "Buzz",
                "Tiana",
            ]
        ),
        why="",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="Every must-do is planned or reported as unmet; none is dropped silently",
        ),
    ),
    Scenario(
        id="salida_en_25_min",
        number=26,
        area="Constraints and feasibility",
        severity="critical",
        title="Guests leave in 25 minutes",
        what="Feasibility: a very short visit should get a short plan or a clear refusal.",
        steps=(
            "Guests (2 adults) want Space Mountain and Haunted Mansion and leave 25 minutes from now",
            "Look at what the app does",
        ),
        setup="Departure = park time + 25 minutes",
        messages=lambda: (
            f"We are 2 adults. {H}We want Space Mountain and Haunted Mansion. We leave at {soon()}.",
        ),
        expected="A short plan or a clear refusal; nothing starting in the past.",
        judge=j_past,
        why="Same root cause as scenario 12: the plan starts at park.opening_time (08:05).",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A plan never starts in the past; a very short window gives a short plan or a clear refusal",
        ),
    ),
    Scenario(
        id="almuerzo_de_madrugada",
        number=27,
        area="Constraints and feasibility",
        severity="normal",
        title="Lunch window set in the middle of the night (3-4 AM)",
        what="Constraints: a lunch outside the park hours cannot be satisfied and must be reported.",
        steps=(
            "Guests (2 adults) want Dumbo, lunch between 3 AM and 4 AM, leave at 10 PM",
            "Guests confirm",
            "The app builds and checks a plan",
        ),
        setup="Lunch 3 AM to 4 AM",
        messages=(
            (
                "We are 2 adults. "
                + H
                + "We want Dumbo. Lunch between 3 AM and 4 AM. We leave at 10 PM."
            ),
        ),
        expected="The plan is rejected with a reason before anything is proposed.",
        judge=j_stops_at_check("LUNCH_WINDOW"),
        why="",
        criteria=Criteria(
            workflow="A lunch window outside the park day stops at the checker and nothing is proposed",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="LUNCH_WINDOW: the plan is rejected with the rule that failed",
        ),
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)],
                must_do=["Teacups"],
                departure="22:00",
                lunch=("03:00", "04:00"),
            )
        ),
    ),
    Scenario(
        id="almuerzo_tras_la_salida",
        number=28,
        area="Constraints and feasibility",
        severity="normal",
        title="Lunch window falls after the departure time",
        what="Constraints: lunch at 1-2 PM while leaving at 11 AM is a contradiction the app must raise with the guests.",
        steps=(
            "Guests (2 adults) want Dumbo, lunch 1-2 PM, leave at 11 AM",
            "The app asks which of the two times should change",
        ),
        setup="Lunch 1-2 PM, leave at 11 AM",
        messages=(
            (
                "We are 2 adults. "
                + H
                + "We want Dumbo. Lunch 1 PM to 2 PM. We leave at 11 AM."
            ),
        ),
        expected="The app asks which of the two times to change; no plan is built or proposed.",
        judge=j_asks,
        why="",
        criteria=Criteria(
            workflow="A lunch window after the departure time stops at ELICIT with a question; no planning stage runs",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A contradiction between lunch and departure is raised with the guests before planning",
        ),
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)],
                must_do=["Teacups"],
                departure="11:00",
                lunch=("13:00", "14:00"),
            )
        ),
    ),
    Scenario(
        id="show_como_mustdo",
        number=30,
        area="Constraints and feasibility",
        severity="normal",
        title="Guests ask for the parade and fireworks as must-dos",
        what="Limits: shows run at fixed times and are not rides; the app should explain how it handles them.",
        steps=(
            "Guests (2 adults) say they definitely want the Festival of Fantasy Parade and Happily Ever After fireworks, leave at 11 PM",
            "Look at what the app does next",
        ),
        setup="Parade and fireworks as must-do",
        messages=(
            (
                "We are 2 adults. "
                + H
                + "We definitely want to see the Festival of Fantasy Parade and "
                "Happily Ever After fireworks. We leave at 11 PM."
            ),
        ),
        expected="The guests get a clear explanation that shows have fixed showtimes and cannot be planned as stops.",
        judge=j_show,
        why="",
        criteria=Criteria(
            workflow="",
            llm_eval="The explanation says why shows are not plannable stops and does not invent a showtime",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="Shows have fixed showtimes and are not plannable stops",
        ),
    ),
    Scenario(
        id="presupuesto_caminata_minimo",
        number=32,
        area="Constraints and feasibility",
        severity="normal",
        title="Walking budget far too small for the wanted rides",
        what="Constraints: three far-apart rides cannot fit in 10 minutes of walking.",
        steps=(
            "Guests (2 adults) want Space Mountain, Seven Dwarfs Mine Train and Peter Pan's Flight, can walk 10 minutes in total, leave at 10 PM",
            "Guests confirm",
            "The app builds and checks a plan",
        ),
        setup="10 minutes of walking for three must-dos",
        messages=(
            (
                "We are 2 adults. "
                + H
                + "We want Space Mountain, Seven Dwarfs Mine Train and Peter Pan's Flight. "
                "We can only walk 10 minutes in total. We leave at 10 PM."
            ),
        ),
        expected="The plan stays within the walking budget, or the failure is reported to the guests.",
        judge=j_stops_at_check("WALKING_BUDGET"),
        why="",
        criteria=Criteria(
            workflow="A walking budget that cannot hold the stops stops at the checker",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="WALKING_BUDGET: the plan is rejected with the rule that failed",
        ),
        offline=Offline(
            extraction=extraction(
                [adult(170), adult(170)],
                must_do=["TRON", "Space Mountain", "Haunted Mansion"],
                departure="22:00",
                walking_budget=10,
            )
        ),
    ),
    Scenario(
        id="evitar_casi_todo",
        number=33,
        area="Constraints and feasibility",
        severity="normal",
        title="Guests avoid almost every ride",
        what="Preferences: with 12 rides avoided, the plan must be built from what remains.",
        steps=(
            "Guests (2 adults) want Dumbo and avoid 12 popular rides, leave at 10 PM",
            "Guests confirm",
            "The app builds a plan",
        ),
        setup="12 avoided rides",
        messages=(
            (
                "We are 2 adults. "
                + H
                + "We want Dumbo. Avoid Space Mountain, TRON, Big Thunder, Haunted Mansion, "
                "Pirates, Jungle Cruise, Buzz Lightyear, Peter Pan, Tiana's Bayou, Seven Dwarfs, Winnie the Pooh, "
                "Mad Tea Party. We leave at 10 PM."
            ),
        ),
        expected="A valid plan that contains none of the avoided rides.",
        judge=j_avoid(
            [
                "Space Mountain",
                "TRON",
                "Big Thunder",
                "Haunted Mansion",
                "Pirates",
                "Jungle Cruise",
                "Buzz",
                "Peter Pan",
                "Tiana",
                "Seven Dwarfs",
                "Pooh",
                "Mad Tea",
            ]
        ),
        why="",
        criteria=Criteria(
            workflow="",
            llm_eval="",
            observability="One trace for the run with a span per executed node; the pause for the guests is not an error",
            business_rules="A plan is built from what remains and contains no avoided ride",
        ),
    ),
)
