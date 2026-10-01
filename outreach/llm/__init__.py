"""Pick a model with LLM_PROVIDER: fake, ollama or anthropic."""

from __future__ import annotations

from ..config import Settings
from .base import LLM, LLMError, LLMReply

__all__ = ["LLM", "LLMError", "LLMReply", "make_llm", "describe"]


def make_llm(settings: Settings) -> LLM:
    provider = settings.llm_provider
    if provider == "fake":
        from .fake import TemplateLLM

        return TemplateLLM()
    if provider == "ollama":
        from .ollama import OllamaLLM

        return OllamaLLM(settings.ollama_url, settings.ollama_model)
    if provider == "anthropic":
        from .anthropic_llm import AnthropicLLM

        return AnthropicLLM(settings.anthropic_model)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use fake, ollama or anthropic.")


def describe(settings: Settings) -> str:
    return {
        "fake": "fake (template writer, no AI)",
        "ollama": f"ollama ({settings.ollama_model}, local)",
        "anthropic": f"anthropic ({settings.anthropic_model})",
    }.get(settings.llm_provider, settings.llm_provider)
