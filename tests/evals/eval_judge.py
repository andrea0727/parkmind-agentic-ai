"""Claude as the DeepEval judge, built on the same chat client and settings as the agents."""

import json
import re
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

pytest.importorskip("deepeval")

from deepeval.models import DeepEvalBaseLLM

from parkmind.config.settings import settings

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class JudgeUnavailableError(RuntimeError):
    """No key is configured, so the judge cannot be called."""


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content
    )


def _json_object(raw: str) -> str:
    unfenced = _FENCE.sub("", raw.strip())
    start, end = unfenced.find("{"), unfenced.rfind("}")
    return unfenced[start : end + 1] if 0 <= start < end else unfenced


def _with_schema_hint(prompt: str, schema: type[BaseModel]) -> str:
    return (
        f"{prompt}\n\nReply with one JSON object and nothing else, matching this "
        f"JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )


class ClaudeJudge(DeepEvalBaseLLM):
    """The client is built on first use, so importing or constructing needs no key."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        chat: Any = None,
    ) -> None:
        self._model_id = model or settings.EXPLAIN_MODEL
        self._api_key = api_key if api_key is not None else settings.ANTHROPIC_API_KEY
        self.name = self._model_id
        self.model = chat

    def load_model(self) -> Any:
        if self.model is None:
            if not self._api_key:
                raise JudgeUnavailableError("no ANTHROPIC_API_KEY is configured")
            from langchain_anthropic import ChatAnthropic

            self.model = ChatAnthropic(
                model_name=self._model_id,
                api_key=SecretStr(self._api_key),
                temperature=0,
                max_tokens_to_sample=2000,
                timeout=60,
                max_retries=2,
                stop=None,
            )
        return self.model

    def get_model_name(self) -> str:
        return self._model_id

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        message = self.load_model().invoke(self._prompt(prompt, schema))
        return self._reply(_text(message.content), schema)

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> Any:
        message = await self.load_model().ainvoke(self._prompt(prompt, schema))
        return self._reply(_text(message.content), schema)

    @staticmethod
    def _prompt(prompt: str, schema: type[BaseModel] | None) -> str:
        return prompt if schema is None else _with_schema_hint(prompt, schema)

    @staticmethod
    def _reply(text: str, schema: type[BaseModel] | None) -> Any:
        if schema is None:
            return text
        return schema.model_validate_json(_json_object(text))
