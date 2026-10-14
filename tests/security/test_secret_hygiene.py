"""Secrets stay out of what guests see and out of the repository."""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from parkmind.agents.elicit_agent import AnthropicExtractor, ExtractorUnavailableError
from parkmind.core.contracts import RuleId
from parkmind.core.conversation import ServiceUnavailableError
from parkmind.services.clients.observability.langfuse_tracing import (
    MASKED,
    mask_for_trace,
)
from parkmind.services.use_cases.planner_queries import public_failure
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError
from security.sec_corpus import (
    SECRET_API_KEY,
    SECRET_PASSWORD,
    SECRET_POSTGRES_URL,
    SECRETS,
)
from security.sec_harness import BENIGN_EXTRACTION, BENIGN_MESSAGE, make_rig

REPO = Path(__file__).resolve().parents[2]
MAX_SCANNED_BYTES = 2_000_000
API_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
DB_URL_PATTERN = re.compile(
    r"postgres(?:ql)?(?:\+\w+)?://([^:@/\s'\"]+):([^@\s/'\"]+)@"
)
PLACEHOLDER_PASSWORDS = frozenset(
    {
        "parkmind",
        "postgres",
        "pass",
        "password",
        "secret",
        "changeme",
        "p",
        "test",
        "example",
    }
)


def _is_placeholder(password: str) -> bool:
    lowered = password.lower()
    return (
        lowered in PLACEHOLDER_PASSWORDS
        or lowered.startswith(("$", "<", "{", "%", "..."))
        or set(lowered) <= {"x", "*", "."}
    )


def secret_findings(text: str) -> list[str]:
    """Kinds of committed secrets found in ``text``; never the secrets themselves."""
    kinds = ["anthropic_api_key"] if API_KEY_PATTERN.search(text) else []
    for match in DB_URL_PATTERN.finditer(text):
        if not _is_placeholder(match.group(2)):
            kinds.append("database_password")
    return kinds


def _tracked_files() -> list[Path]:
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available or this is not a git checkout")
    return [REPO / name for name in listing.decode().split("\0") if name]


def _readable(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_SCANNED_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data else data.decode("utf-8", errors="ignore")


def test_the_scanner_flags_a_real_looking_key_and_password() -> None:
    assert secret_findings(f"ANTHROPIC_API_KEY={SECRET_API_KEY}") == [
        "anthropic_api_key"
    ]
    assert secret_findings(f"DATABASE_URL={SECRET_POSTGRES_URL}") == [
        "database_password"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "ANTHROPIC_API_KEY=" + "sk-" + "ant-...",
        "ANTHROPIC_API_KEY=",
        "DATABASE_URL=postgresql://parkmind:parkmind@localhost:5432/parkmind",
        "DATABASE_URL=postgres://user:secret@db.internal:5432/x",
        "DATABASE_URL=postgresql://u:${DB_PASSWORD}@h/db",
        "DATABASE_URL=postgresql://u:<password>@h/db",
        "DATABASE_URL=postgresql+psycopg://u:p@h/db",
    ],
)
def test_the_scanner_accepts_placeholders(line: str) -> None:
    assert secret_findings(line) == []


def test_no_tracked_file_contains_an_api_key_or_a_database_password() -> None:
    findings = []
    for path in _tracked_files():
        content = _readable(path)
        if content is None:
            continue
        for kind in secret_findings(content):
            findings.append(f"{path.relative_to(REPO)}: {kind}")

    assert findings == []


def test_the_environment_file_is_not_tracked_and_is_ignored() -> None:
    tracked = {p.relative_to(REPO).as_posix() for p in _tracked_files()}
    gitignore = _readable(REPO / ".gitignore") or ""

    assert ".env" not in tracked
    assert ".env.example" in tracked
    assert any(line.strip() == ".env" for line in gitignore.splitlines())


def _assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


class _DownModel:
    """A chat model whose transport fails with a message that carries secrets."""

    def invoke(self, messages: Any) -> Any:
        raise ConnectionError(
            f"cannot reach {SECRET_POSTGRES_URL} using {SECRET_API_KEY}"
        )


def _extractor_with_failing_model() -> AnthropicExtractor:
    extractor = AnthropicExtractor(api_key="placeholder-key")
    extractor._llm = _DownModel()
    return extractor


def test_the_extractors_failure_names_only_the_error_type() -> None:
    with pytest.raises(ExtractorUnavailableError) as raised:
        _extractor_with_failing_model().extract(["hello"])

    assert str(raised.value) == "ConnectionError"
    _assert_clean(repr(raised.value.args))


def test_a_model_outage_reaches_the_guests_without_secrets() -> None:
    rig = make_rig(extractor=_extractor_with_failing_model())

    result = rig.start(BENIGN_MESSAGE)

    assert "__interrupt__" not in result
    _assert_clean(repr(result["messages"]))
    _assert_clean(repr({k: v for k, v in rig.values().items() if k != "messages"}))


def test_secrets_in_a_rejected_extraction_are_not_shown_to_the_guests() -> None:
    bad = {
        **BENIGN_EXTRACTION,
        "departure_time": SECRET_API_KEY,
        "must_do": [SECRET_POSTGRES_URL],
    }
    rig = make_rig(bad)

    result = rig.start(BENIGN_MESSAGE)

    assert rig.extractor.calls[-1][0] == [BENIGN_MESSAGE]
    _assert_clean(repr(result))
    _assert_clean(repr(rig.values()))


def test_secrets_in_the_values_of_a_rejected_extraction_stay_out_of_the_repair_hint() -> (
    None
):
    bad = {
        **BENIGN_EXTRACTION,
        "departure_time": SECRET_API_KEY,
        "party_size": SECRET_PASSWORD,
    }
    rig = make_rig(bad)

    rig.start(BENIGN_MESSAGE)

    hints = [hint for _, hint in rig.extractor.calls if hint]
    assert hints
    for hint in hints:
        _assert_clean(hint)


class _LeakyNarrator:
    def narrate(self, payload_json: str, repair_hint: str | None = None) -> Any:
        raise RuntimeError(f"upstream said: {SECRET_API_KEY} {SECRET_POSTGRES_URL}")


def test_a_narrator_failure_leaves_only_its_type_in_the_logs_and_nothing_in_the_plan(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = make_rig(BENIGN_EXTRACTION, narrator=_LeakyNarrator())
    caplog.set_level(logging.DEBUG)
    rig.start(BENIGN_MESSAGE)

    result = rig.confirm()

    _assert_clean(caplog.text)
    _assert_clean(repr(result))
    _assert_clean(" ".join(p.explanation for p in rig.proposals()))
    assert "RuntimeError" in caplog.text


def test_an_outage_with_a_connection_string_reaches_the_service_caller_as_the_standard_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conversation_support import make_rig as make_service_rig

    rig = make_service_rig()
    thread = rig.service.start()
    graph = rig.service._graphs["initial"]

    def down(*args: Any, **kwargs: Any) -> Any:
        raise PlanningUnavailableError(f"{SECRET_POSTGRES_URL} refused")

    monkeypatch.setattr(graph, "invoke", down)

    with pytest.raises(ServiceUnavailableError) as raised:
        rig.service.say(thread, BENIGN_MESSAGE, "en")

    _assert_clean(str(raised.value))
    assert "Nothing was proposed" in str(raised.value)


def _nest(depth: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    node = root
    for _ in range(depth):
        child: dict[str, Any] = {}
        node["next"] = child
        node = child
    node["leaf"] = SECRET_PASSWORD
    return root


class _HostileRepr:
    def __repr__(self) -> str:
        raise RuntimeError(SECRET_API_KEY)

    def __str__(self) -> str:
        raise RuntimeError(SECRET_API_KEY)


def test_the_trace_mask_hides_a_structure_too_deep_to_read() -> None:
    masked = mask_for_trace(data=_nest(5000))

    assert SECRET_PASSWORD not in repr(masked)
    assert MASKED in repr(masked)


def test_the_trace_mask_terminates_on_a_cycle() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)

    masked = mask_for_trace(data={"loop": cycle})

    assert MASKED in repr(masked)


def test_the_trace_mask_never_calls_the_repr_of_an_unknown_object() -> None:
    masked = mask_for_trace(data={"thing": _HostileRepr(), "many": [_HostileRepr()]})

    assert masked == {"thing": "<_HostileRepr>", "many": ["<_HostileRepr>"]}


@pytest.mark.parametrize(
    "message",
    [
        {"role": "user", "content": SECRET_API_KEY},
        {"type": "human", "content": SECRET_POSTGRES_URL, "extra": SECRET_PASSWORD},
        {
            "role": "assistant",
            "content": SECRET_API_KEY,
            "tool_calls": [SECRET_PASSWORD],
        },
    ],
    ids=["user", "typed", "tool_calls"],
)
def test_the_trace_mask_drops_the_text_of_a_message_dict(
    message: dict[str, Any],
) -> None:
    _assert_clean(repr(mask_for_trace(data=[{"inputs": message}])))


@pytest.mark.parametrize(
    "key",
    [
        "correction",
        "label",
        "echo",
        "explanation",
        "accessibility",
        "mobility_requirements",
    ],
)
def test_the_trace_mask_hides_private_fields_wherever_they_sit(key: str) -> None:
    masked = mask_for_trace(data={"outer": [{"inner": {key: SECRET_PASSWORD}}]})

    _assert_clean(repr(masked))


def test_the_trace_mask_hides_a_resume_answer_under_its_interrupt_id() -> None:
    masked = mask_for_trace(data={"resume": {"int-1": SECRET_API_KEY}})

    _assert_clean(repr(masked))


def test_the_trace_mask_hides_the_elicit_prompts_guest_block() -> None:
    block = f"Guest messages, oldest first:\n1. {SECRET_PASSWORD}"

    assert mask_for_trace(data=block) == MASKED


@pytest.mark.parametrize(
    "failure",
    [
        f"Infeasible constraint set: {RuleId.ACCESSIBILITY.value} - guest g2 "
        + SECRET_PASSWORD
        + "\n" * 3,
        f"Infeasible constraint set: {RuleId.RIDE_RESTRICTION.value} - "
        + "A" * 100_000,
        "The replanned day does not pass the constraint checker: " + SECRET_PASSWORD,
        f"Infeasible constraint set: {RuleId.ACCESSIBILITY.value} - (((" + "(" * 1000,
    ],
    ids=["accessibility", "very_long", "day_gate", "regex_bait"],
)
def test_a_failure_with_private_details_is_reduced_to_its_rule(failure: str) -> None:
    public = public_failure(failure)

    assert public is not None
    assert SECRET_PASSWORD not in public
    assert len(public) < 200


def test_a_failure_without_a_private_rule_is_returned_as_written() -> None:
    assert public_failure(None) is None
    assert public_failure(
        f"Infeasible constraint set: {RuleId.HEIGHT.value} - stop 3"
    ) == (f"Infeasible constraint set: {RuleId.HEIGHT.value} - stop 3")
