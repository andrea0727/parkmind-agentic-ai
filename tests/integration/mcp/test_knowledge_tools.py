"""``knowledge.*`` tools over MCP (P0-26 Done-when), offline.

Without a semantic search port the keyword fallback answers, and says so; the
pgvector-backed path is covered in tests/integration/postgres.
"""

from typing import Any

from mcp_support import Deps, call_tool, list_tools

from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.use_cases.check_accessibility import check_flags

KNOWLEDGE_TOOLS = {
    "knowledge.search_policies",
    "knowledge.find_similar_attractions",
    "knowledge.check_accessibility",
}
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
SMALL_WORLD = "f5aad2d4-a419-4384-bd9a-42f86385c750"
TRANSFER = "REQUIRES_TRANSFER_FROM_WHEELCHAIR"


def _deps() -> Deps:
    return Deps(collect_at=None)


def _ok(result: Any) -> dict[str, Any]:
    assert result.is_error is False, result.content[0].text
    return result.structured_content


def test_knowledge_tools_have_typed_schemas() -> None:
    tools = {t.name: t for t in list_tools(_deps().registry())}

    assert KNOWLEDGE_TOOLS <= set(tools)
    for name in KNOWLEDGE_TOOLS:
        assert tools[name].input_schema["type"] == "object", name
        assert set(tools[name].output_schema["properties"]) == {"data", "provenance"}, (
            name
        )
        assert tools[name].annotations.read_only_hint is True, name


def test_check_accessibility_tool_delegates_and_fails_closed() -> None:
    payload = _ok(
        call_tool(
            _deps().registry(),
            "knowledge.check_accessibility",
            {
                "attraction_ids": [BIG_THUNDER, SMALL_WORLD, "no-such"],
                "flags": [TRANSFER],
                "guest_id": "g4",
            },
        )
    )

    store = magic_kingdom_knowledge_store()
    expected = [
        check_flags("g4", frozenset({TRANSFER}), a, store).model_dump(mode="json")  # type: ignore[arg-type]
        for a in (BIG_THUNDER, SMALL_WORLD, "no-such")
    ]
    assert payload["data"]["checks"] == expected
    by_id = {c["attraction_id"]: c for c in payload["data"]["checks"]}
    assert by_id[BIG_THUNDER]["eligible"] is False
    assert by_id[BIG_THUNDER]["conflicting_requirement"] == TRANSFER
    assert by_id[SMALL_WORLD]["eligible"] is True
    assert by_id["no-such"]["eligible"] is False  # no notice on file
    assert by_id["no-such"]["provenance"]["notice_on_file"] is False
    assert payload["provenance"]["source"] == "park_safety_notice"
    assert payload["provenance"]["version"] == store.corpus_version


def test_check_accessibility_rejects_free_text_flags() -> None:
    """Only the closed RideRestriction set crosses the boundary (section 12)."""
    result = call_tool(
        _deps().registry(),
        "knowledge.check_accessibility",
        {"attraction_ids": [BIG_THUNDER], "flags": ["no heart problems"]},
    )

    assert result.is_error is True


def test_search_policies_quotes_passages_with_their_source() -> None:
    payload = _ok(
        call_tool(
            _deps().registry(),
            "knowledge.search_policies",
            {"query": "Rider Switch", "k": 3},
        )
    )

    passage = payload["data"]["passages"][0]
    assert "Rider Switch" in passage["title"]
    assert passage["source_url"].startswith("https://disneyworld.disney.go.com/")
    assert passage["reviewed_on"] == "2026-10-10"
    provenance = payload["provenance"]
    assert (
        provenance["source"] == "knowledge_corpus"
        and provenance["version"] == "2026-10-10"
    )
    assert provenance["strategy"] == "keyword"
    assert provenance["degraded"] == "semantic_search_not_configured"


def test_find_similar_less_intense_over_mcp() -> None:
    payload = _ok(
        call_tool(
            _deps().registry(),
            "knowledge.find_similar_attractions",
            {"attraction_id": BIG_THUNDER, "k": 4, "less_intense": True},
        )
    )

    assert payload["data"]["reference_intensity"] == "high"
    assert payload["data"]["attractions"]
    assert {a["intensity"] for a in payload["data"]["attractions"]} <= {
        "low",
        "moderate",
    }


def test_find_similar_unknown_attraction_is_not_found() -> None:
    result = call_tool(
        _deps().registry(),
        "knowledge.find_similar_attractions",
        {"attraction_id": "no-such"},
    )

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "NOT_FOUND"


def test_find_similar_needs_exactly_one_reference() -> None:
    result = call_tool(
        _deps().registry(),
        "knowledge.find_similar_attractions",
        {"attraction_id": BIG_THUNDER, "text": "montaña rusa"},
    )

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENT"
