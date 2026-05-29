import logging
import os

import httpx

from .base import LLMProvider

logger = logging.getLogger(__name__)

_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
_FALLBACK_MODEL = os.getenv("OPENROUTER_FALLBACK_MODEL", "google/gemini-2.0-flash-exp:free")
_BRAND_URL = os.getenv("BRAND_URL", "https://alldoors.in")


class OpenRouterProvider(LLMProvider):
    def complete(self, system: str, user: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        def _post(model: str) -> httpx.Response:
            return httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {_API_KEY}",
                    "HTTP-Referer": _BRAND_URL,
                    "X-Title": "IQOL CROWDREPLY",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7,
                    "response_format": {"type": "json_object"},
                    "max_tokens": 1200,
                },
                timeout=60,
            )

        resp = _post(_MODEL)
        if resp.status_code in (429,) or resp.status_code >= 500:
            logger.warning("OpenRouter %s on %s — retrying with fallback", resp.status_code, _MODEL)
            resp = _post(_FALLBACK_MODEL)

        if resp.status_code in (429,) or resp.status_code >= 500:
            from .base import LLMTransientError
            raise LLMTransientError(
                f"OpenRouter transient failure ({resp.status_code}) after primary and fallback"
            )

        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
