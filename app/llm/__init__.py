import os

from dotenv import load_dotenv

from .base import LLMProvider, LLMTransientError, LLMDailyQuotaError

__all__ = ["get_provider", "LLMTransientError", "LLMDailyQuotaError"]

_provider_instance: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

    if provider == "gemini":
        from .gemini_provider import GeminiProvider
        _provider_instance = GeminiProvider()
    elif provider == "openrouter":
        from .openrouter_provider import OpenRouterProvider
        _provider_instance = OpenRouterProvider()
    elif provider == "groq":
        from .groq_provider import GroqProvider
        _provider_instance = GroqProvider()
    else:
        from .gemini_provider import GeminiProvider
        _provider_instance = GeminiProvider()

    return _provider_instance
