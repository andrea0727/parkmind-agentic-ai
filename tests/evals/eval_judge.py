"""Claude as the DeepEval judge, built on the same chat client and settings as the agents."""

import json
import os
import re
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

pytest.importorskip("deepeval")

from deepeval.models import DeepEvalBaseLLM

from parkmind.config.settings import settings

JUDGE_MODEL = os.getenv("PARKMIND_JUDGE_MODEL", "claude-sonnet-5")
"""A model other than the ones being judged (they default to Haiku): a model grading its own output is lenient."""

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
        self._model_id = model or JUDGE_MODEL
        self._api_key = api_key if api_key is not None else settings.ANTHROPIC_API_KEY
        self.name = self._model_id
        self.model = chat
        self._chat_injected = chat is not None

    def load_model(self) -> Any:
        if self.model is None:
            if not self._api_key:
                raise JudgeUnavailableError("no ANTHROPIC_API_KEY is configured")
            self.model = self._client(temperature=0)
        return self.model

    def _client(self, **extra: Any) -> Any:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=self._model_id,
            api_key=SecretStr(self._api_key or ""),
            max_tokens_to_sample=2000,
            timeout=60,
            max_retries=2,
            stop=None,
            **extra,
        )

    def _without_temperature(self, error: Exception) -> bool:
        """Newer models reject `temperature`; rebuild the client without it and retry once."""
        if "temperature" not in str(error) or self._chat_injected:
            return False
        self.model = self._client()
        return True

    def get_model_name(self) -> str:
        return self._model_id

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        try:
            message = self.load_model().invoke(self._prompt(prompt, schema))
        except Exception as error:
            if not self._without_temperature(error):
                raise
            message = self.load_model().invoke(self._prompt(prompt, schema))
        return self._reply(_text(message.content), schema)

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> Any:
        try:
            message = await self.load_model().ainvoke(self._prompt(prompt, schema))
        except Exception as error:
            if not self._without_temperature(error):
                raise
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
