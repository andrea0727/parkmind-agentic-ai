"""Real conversations against the live models, in two arms: the fixed question template and the LLM question writer."""

from datetime import datetime

from conversation_support import make_rig

from parkmind.agents.ask_agent import AnthropicQuestionWriter
from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.core.contracts import PARK_TZ

OPEN_DAY = datetime(2026, 10, 6, 10, 0, tzinfo=PARK_TZ)

DIALOGUES = {
    "unsure": ["hola", "no sé", "no sé", "somos 2 adultos y un niño de 6 años"],
    "english": ["hi", "not sure", "we are 2 adults and a 6 year old"],
    "clear": ["hola", "somos 3 adultos", "llegamos por la tarde"],
}

FIXED = "fixed-template"
LLM = "llm-ask"
ARMS = (FIXED, LLM)


def converse(guest_turns: list[str], arm: str = LLM) -> list[tuple[str, str]]:
    rig = make_rig(
        extractor=AnthropicExtractor(),
        asker=AnthropicQuestionWriter() if arm == LLM else None,
        clock=lambda: OPEN_DAY,
    )
    thread = rig.service.start()
    turns: list[tuple[str, str]] = []
    for text in guest_turns:
        turn = rig.service.say(thread, text)
        reply = turn.question or (turn.messages[-1] if turn.messages else "")
        turns += [("guest", text), ("assistant", reply)]
    return turns
