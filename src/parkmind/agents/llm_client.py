"""The one place that knows which LLM provider is behind the agents.

``LLM_PROVIDER`` (anthropic, openai or google) picks the LangChain chat model; the agents
only see a chat model they bind one forced output schema to, so they do not change with it.
Provider packages beyond Anthropic are optional extras and are imported on first use.
"""

from typing import Any

from pydantic import SecretStr

from parkmind.config.settings import PROVIDERS, settings

_INSTALL_HINT = {
    "anthropic": "poetry install",
    "openai": "poetry install -E openai",
    "google": "poetry install -E google",
}


def _import_error(provider: str, exc: ImportError) -> ImportError:
    return ImportError(
        f"LLM_PROVIDER={provider} needs its provider package ({exc.name}); "
        f"install it with `{_INSTALL_HINT[provider]}`"
    )


def build_chat_model(
    *,
    model: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    provider: str | None = None,
) -> Any:
    """A chat model with no client-side retries: ``invoke_counted`` retries, so each call is counted."""
    provider = provider or settings.LLM_PROVIDER
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported LLM_PROVIDER {provider!r}")
    try:
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model_name=model,
                api_key=SecretStr(api_key),
                temperature=temperature,
                max_tokens_to_sample=max_tokens,
                timeout=timeout,
                max_retries=0,
                stop=None,
            )
        if provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model,
                api_key=SecretStr(api_key),
                temperature=temperature,
                max_completion_tokens=max_tokens,
                timeout=timeout,
                max_retries=0,
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=SecretStr(api_key),
            temperature=temperature,
            max_output_tokens=max_tokens,
            timeout=timeout,
            max_retries=0,
        )
    except ImportError as exc:
        raise _import_error(provider, exc) from exc


def check_provider_installed(provider: str | None = None) -> None:
    """Fail at start-up, not on the first message, when the provider's package is missing."""
    provider = provider or settings.LLM_PROVIDER
    try:
        if provider == "anthropic":
            import langchain_anthropic  # noqa: F401
        elif provider == "openai":
            import langchain_openai  # noqa: F401
        else:
            import langchain_google_genai  # noqa: F401
    except ImportError as exc:
        raise _import_error(provider, exc) from exc


def is_retryable(exc: Exception, provider: str | None = None) -> bool:
    """What the SDK itself used to retry: no connection, timeout, rate limit, server error."""
    provider = provider or settings.LLM_PROVIDER
    if provider == "anthropic":
        import anthropic

        return isinstance(
            exc,
            (
                anthropic.APIConnectionError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            ),
        )
    if provider == "openai":
        import openai

        return isinstance(
            exc,
            (
                openai.APIConnectionError,
                openai.RateLimitError,
                openai.InternalServerError,
            ),
        )
    import httpx
    from google.genai import errors

    if isinstance(exc, errors.ClientError):
        return exc.code == 429
    return isinstance(exc, (errors.ServerError, httpx.TransportError))
