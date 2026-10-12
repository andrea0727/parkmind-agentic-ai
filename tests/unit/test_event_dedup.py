"""Duplicate events are suppressed across sources (P0-33)."""

from datetime import datetime, timedelta

import factories

from parkmind.core.contracts import PARK_TZ, EventSource, EventType
from parkmind.services.planning.event_dedup import deduplicate

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=PARK_TZ)


def _down(
    source: EventSource,
    minutes: int = 0,
    event_type=EventType.ATTRACTION_DOWN,
    **fields,
):
    return factories.event(
        **{
            "event_id": f"raw-{source.value}-{minutes}",
            "type": event_type,
            "source": source,
            "attraction_id": "tron",
            "timestamp": NOW + timedelta(minutes=minutes),
            **fields,
        }
    )


def _record(history, candidates):
    kept = deduplicate(candidates, history)
    return history + kept, kept


def test_a_monitor_closure_then_a_user_report_of_it_is_one_event():
    history, first = _record([], [_down(EventSource.MONITOR)])
    _, second = _record(history, [_down(EventSource.USER, minutes=50)])

    assert [e.event_id for e in first] == ["ATTRACTION_DOWN:tron:1"]
    assert second == []


def test_a_user_report_then_the_monitor_is_also_one_event():
    history, first = _record([], [_down(EventSource.USER)])
    _, second = _record(history, [_down(EventSource.MONITOR, minutes=3)])

    assert [e.source for e in first] == [EventSource.USER]
    assert second == []


def test_both_sources_in_one_batch_keep_one():
    kept = deduplicate(
        [_down(EventSource.USER, minutes=1), _down(EventSource.MONITOR)], []
    )

    assert [e.event_id for e in kept] == ["ATTRACTION_DOWN:tron:1"]


def test_down_available_down_are_three_episodes():
    history, a = _record([], [_down(EventSource.MONITOR)])
    history, b = _record(
        history, [_down(EventSource.MONITOR, 30, EventType.ATTRACTION_AVAILABLE)]
    )
    history, c = _record(history, [_down(EventSource.MONITOR, 90)])

    assert [e.event_id for e in [*a, *b, *c]] == [
        "ATTRACTION_DOWN:tron:1",
        "ATTRACTION_AVAILABLE:tron:1",
        "ATTRACTION_DOWN:tron:2",
    ]


def test_the_monitor_seeing_a_closure_after_a_wrong_user_report_is_a_new_episode():
    """Review #86 (B1): the user's DOWN was wrong, the ride kept running, and it
    really went down 3 h later. The monitor saw OPERATING -> DOWN, so it is a
    new closure, not the open episode."""
    history, _ = _record([], [_down(EventSource.USER)])
    _, real = _record(history, [_down(EventSource.MONITOR, minutes=180)])

    assert [e.event_id for e in real] == ["ATTRACTION_DOWN:tron:2"]


def test_a_missed_available_does_not_hide_the_next_closure():
    history, _ = _record([], [_down(EventSource.MONITOR)])
    # The AVAILABLE in between was never recorded (no fresh snapshot).
    _, again = _record(history, [_down(EventSource.MONITOR, minutes=120)])

    assert [e.event_id for e in again] == ["ATTRACTION_DOWN:tron:2"]


def test_a_late_user_report_of_a_known_closure_stays_a_duplicate():
    history, _ = _record([], [_down(EventSource.MONITOR)])

    assert deduplicate([_down(EventSource.USER, minutes=180)], history) == []


def test_a_monitor_rerun_on_the_same_snapshots_records_nothing_new():
    history, _ = _record([], [_down(EventSource.MONITOR)])

    assert deduplicate([_down(EventSource.MONITOR)], history) == []


def test_episodes_are_per_attraction():
    kept = deduplicate(
        [_down(EventSource.MONITOR), _down(EventSource.MONITOR, attraction_id="space")],
        [],
    )

    assert sorted(e.event_id for e in kept) == [
        "ATTRACTION_DOWN:space:1",
        "ATTRACTION_DOWN:tron:1",
    ]


def _fatigue(minutes: int, source=EventSource.USER, guest="g2"):
    return factories.event(
        event_id=f"raw-{minutes}",
        type=EventType.GUEST_FATIGUE,
        source=source,
        attraction_id=None,
        guest_id=guest,
        confidence=0.9,
        timestamp=NOW + timedelta(minutes=minutes),
    )


def test_a_moment_repeated_within_the_window_is_a_duplicate_and_later_is_new():
    history, first = _record([], [_fatigue(0)])
    history, again = _record(history, [_fatigue(20)])
    _, later = _record(history, [_fatigue(45)])

    assert len(first) == 1 and again == [] and len(later) == 1
    assert first[0].event_id != later[0].event_id


def test_moments_for_different_subjects_are_kept():
    kept = deduplicate([_fatigue(0, guest="g1"), _fatigue(0, guest="g2")], [])

    assert len(kept) == 2


def test_ids_do_not_depend_on_the_source_or_the_raw_id():
    from_user = deduplicate([_fatigue(0, source=EventSource.USER)], [])
    from_monitor = deduplicate([_fatigue(0, source=EventSource.MONITOR)], [])

    assert (
        from_user[0].event_id
        == from_monitor[0].event_id
        == "GUEST_FATIGUE:g2:20260916T1430Z"
    )


def test_deduplication_is_deterministic_and_leaves_inputs_alone():
    candidates = [_down(EventSource.USER, 1), _fatigue(0), _down(EventSource.MONITOR)]

    assert deduplicate(candidates, []) == deduplicate(list(reversed(candidates)), [])
    assert candidates[0].event_id == "raw-user-1"
