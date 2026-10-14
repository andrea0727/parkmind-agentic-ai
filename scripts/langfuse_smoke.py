"""Send one masked conversation trace to Langfuse, to see tracing work end to end.

    docker compose -f docker-compose.langfuse.yml up -d
    # in .env: PARKMIND_TRACING=langfuse and the three LANGFUSE_* settings
    poetry run python scripts/langfuse_smoke.py          # offline: a fake chat model
    poetry run python scripts/langfuse_smoke.py --llm    # real Claude (needs ANTHROPIC_API_KEY)

It runs the elicitation graph once, through the same masked handler the API uses
(``langfuse_callbacks``): a party description, then a correction at the
confirmation, so the trace shows the graph, its nodes and the LLM call. It needs no
database: the catalog is two attractions in memory. Open the printed URL; the chat
text, the correction and the accessibility data read ``[masked]``.
"""

import argparse
import itertools
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.language_models.fake_chat_models import (
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.core.contracts import Attraction, AttractionCategory
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import build_elicitation_graph
from parkmind.services.clients.observability.langfuse_tracing import (
    flush_tracing,
    langfuse_callbacks,
)
from parkmind.services.use_cases.resolve_attraction_names import (
    AttractionNameResolver,
)

PARK_ID = "smoke"
MESSAGES = [
    "We are two adults and a kid. We want TRON and Space Mountain.",
    "My father can't walk long distances. We leave at 8 PM.",
]
# What the fake model "extracts" (the schema the real model is forced to fill).
EXTRACTION = {
    "party_size": 3,
    "guests": [{"role": "adult"}, {"role": "adult"}, {"role": "child"}],
    "must_do": ["TRON", "Space Mountain"],
    "departure_time": "20:00",
    "accessibility": [{"guest_ref": 2, "mobility_requirements": ["LIMITED_WALKING"]}],
}


class _Catalog:
    def list_attractions(self, park_id: str) -> list[Attraction]:
        return [
            Attraction(
                node_id=node_id,
                name=name,
                category=AttractionCategory.THRILL,
                height_restriction_cm=None,
                typical_wait_minutes=30,
                outdoor=False,
            )
            for node_id, name in (
                ("smoke-tron", "TRON"),
                ("smoke-space", "Space Mountain"),
            )
        ]


class _FakeChat(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "_FakeChat":
        return self


def _offline_model() -> None:
    import langchain_anthropic

    reply = AIMessage(
        content="",
        tool_calls=[
            {"name": "ElicitationExtraction", "args": EXTRACTION, "id": "smoke"}
        ],
    )
    langchain_anthropic.ChatAnthropic = (  # type: ignore[misc]
        lambda **_: _FakeChat(messages=itertools.repeat(reply))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--llm", action="store_true", help="use the real Claude model")
    args = parser.parse_args()

    callbacks = langfuse_callbacks()
    if not callbacks:
        print("Tracing is off: set PARKMIND_TRACING=langfuse (see .env.example).")
        return 1
    if args.llm:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--llm needs ANTHROPIC_API_KEY.")
            return 1
    else:
        _offline_model()

    thread_id = f"smoke-{uuid4().hex[:8]}"
    graph = build_elicitation_graph(
        AnthropicExtractor(api_key=os.environ.get("ANTHROPIC_API_KEY") or "offline"),
        checkpointer=default_checkpointer(),
        names=AttractionNameResolver(_Catalog(), PARK_ID),  # type: ignore[arg-type]
    )
    config: Any = {
        "configurable": {"thread_id": thread_id},
        "callbacks": callbacks,
        "metadata": {"langfuse_session_id": thread_id, "langfuse_tags": ["smoke"]},
    }
    graph.invoke(
        {
            "thread_id": thread_id,
            "messages": [HumanMessage(content=m) for m in MESSAGES],
        },
        config=config,
    )
    graph.invoke(
        Command(
            resume={"confirmed": False, "correction": "Actually we leave at 7 PM."}
        ),
        config=config,
    )
    from langfuse import get_client

    trace_id = getattr(callbacks[0], "last_trace_id", None)
    client = get_client(public_key=os.environ["LANGFUSE_PUBLIC_KEY"])
    url = client.get_trace_url(trace_id=trace_id) if trace_id else None
    flush_tracing()

    print(f"Sent session {thread_id} to {os.environ['LANGFUSE_BASE_URL']}.")
    print(f"Last trace: {url}" if url else "Open the project's Sessions tab to see it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
