"""Real chat-model construction via LangChain's provider-agnostic `init_chat_model`."""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from claimpilot.config import get_settings


def parse_model_spec(spec: str) -> tuple[str, str]:
    """Split "provider:model" on the first colon: "ollama:gpt-oss:20b" -> ollama, gpt-oss:20b."""
    provider, sep, model = spec.partition(":")
    if not sep or not provider or not model:
        raise ValueError(f"Model spec must look like 'provider:model', got {spec!r}")
    return provider, model


@lru_cache
def build_chat_model(spec: str) -> BaseChatModel:
    provider, model = parse_model_spec(spec)
    settings = get_settings()
    # Retries/timeouts are handled uniformly by LLMClient, so disable provider-level retries
    # where the integration supports the kwarg.
    kwargs: dict = {"temperature": settings.llm_temperature}
    if provider in {"openai", "anthropic"}:
        kwargs["max_retries"] = 0
    if provider == "ollama":
        kwargs["num_ctx"] = settings.llm_num_ctx
        kwargs["num_predict"] = settings.llm_num_predict
        if settings.llm_reasoning is not None:
            r = settings.llm_reasoning.lower()
            kwargs["reasoning"] = {"true": True, "false": False}.get(r, r)
    return init_chat_model(model, model_provider=provider, **kwargs)
