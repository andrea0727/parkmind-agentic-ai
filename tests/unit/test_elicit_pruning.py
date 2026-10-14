from parkmind.agents.elicit_agent import without_empty_accessibility


def test_drops_empty_accessibility_entries() -> None:
    args = {
        "accessibility": [
            {"guest_id": "g1", "mobility_requirements": []},
            {"guest_id": "g2", "mobility_requirements": ["wheelchair"]},
        ]
    }
    out = without_empty_accessibility(args)
    assert [a["guest_id"] for a in out["accessibility"]] == ["g2"]


def test_leaves_args_without_accessibility_untouched() -> None:
    args = {"departure_time": "09:00"}
    assert without_empty_accessibility(args) == args
