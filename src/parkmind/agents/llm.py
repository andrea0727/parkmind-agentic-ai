import os

from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA

load_dotenv()


def get_llm(
    temperature: float = 1,
    max_tokens: int = 16384,
) -> ChatNVIDIA:
    return ChatNVIDIA(
        model=os.environ["PARKMIND_LLM_MODEL"],
        api_key=os.environ["NVIDIA_API_KEY"],
        temperature=temperature,
        top_p=0.95,
        max_tokens=max_tokens,
        reasoning_budget=16384,
        chat_template_kwargs={"enable_thinking": True},
        timeout=180,
    )
