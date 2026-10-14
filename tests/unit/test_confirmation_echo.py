from parkmind.services.use_cases.accessibility_intake import AccessibilityIntakeUseCase
from parkmind.services.use_cases.confirmation_echo import confirmation_echo


def test_the_echo_never_names_accessibility_and_its_guests_travel_apart() -> None:
    result = confirmation_echo(
        ["height:g2: 110", "accessibility:g3", "must_do: TRON", "accessibility:g1"],
        "en",
    )

    assert len(result.echo) == 2
    assert result.echo[0].startswith("Guest 2")
    assert "TRON" in result.echo[1]
    assert not any("accessib" in line.lower() for line in result.echo)
    assert result.accessibility_guests == ("g1", "g3")


def test_the_echo_uses_how_the_guests_call_each_other() -> None:
    result = confirmation_echo(["height:g2: 110"], "es", labels={"g2": "tu hija"})

    assert result.echo[0].startswith("Tu hija mide 110 cm")
    assert result.accessibility_guests == ()


def test_the_intake_use_case_is_where_the_graph_gets_the_echo() -> None:
    result = AccessibilityIntakeUseCase().confirmation(
        ["walking_budget: 180", "accessibility:g2"], "en", solo=False
    )

    assert "180" in result.echo[0]
    assert result.accessibility_guests == ("g2",)
