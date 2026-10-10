import os

from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA

load_dotenv()


def get_llm(temperature: float = 0.0, max_tokens: int = 8192) -> ChatNVIDIA:
    # A reasoning model spends tokens thinking before it answers; a low cap cuts it off
    # before the tool call is emitted.
    return ChatNVIDIA(
        model=os.environ["PARKMIND_LLM_MODEL"],
        api_key=os.environ["NVIDIA_API_KEY"],
        temperature=temperature,
        max_completion_tokens=max_tokens,
        timeout=180,
    )
