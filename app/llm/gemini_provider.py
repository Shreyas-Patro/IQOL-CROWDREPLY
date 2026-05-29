import logging
import os
import re
import time

from .base import LLMProvider, LLMTransientError, LLMDailyQuotaError

logger = logging.getLogger(__name__)

_API_KEY = os.getenv("GEMINI_API_KEY", "")
_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
_RATE_LIMIT_BACKOFF = (5, 10, 20)


def _is_daily_quota(exc) -> bool:
    msg = str(exc).lower()
    # Google embeds quota IDs like "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    return "perday" in msg or "per_day" in msg or "generaterequestsperday" in msg


def _parse_retry_delay(exc) -> int | None:
    """Extract retryDelay seconds from a Gemini error, capped at 120s."""
    msg = str(exc)
    # JSON/dict format: 'retryDelay': '9s'  or  "retryDelay": "9s"
    m = re.search(r"retrydelay[^0-9]*(\d+)\s*s", msg, re.IGNORECASE)
    if m:
        return min(int(m.group(1)), 120)
    # Proto format: retry_delay { seconds: 9 }
    m = re.search(r"retry_delay\s*\{[^}]*seconds:\s*(\d+)", msg, re.IGNORECASE)
    if m:
        return min(int(m.group(1)), 120)
    return None


class GeminiProvider(LLMProvider):
    def __init__(self):
        from google import genai
        self._client = genai.Client(api_key=_API_KEY)

    def complete(self, system: str, user: str) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.7,
            max_output_tokens=2000,
        )

        last_exc = None
        for attempt, fallback_wait in enumerate((*_RATE_LIMIT_BACKOFF, None)):
            try:
                response = self._client.models.generate_content(
                    model=_MODEL, contents=user, config=config,
                )
                return response.text
            except Exception as exc:
                msg = str(exc).lower()
                is_exhausted = "resource_exhausted" in msg or "429" in msg or "quota" in msg
                is_overload = "503" in msg or "overloaded" in msg or "service_unavailable" in msg

                if is_exhausted and _is_daily_quota(exc):
                    raise LLMDailyQuotaError(
                        "Daily Gemini quota exhausted — aborting, resume tomorrow or switch provider"
                    ) from exc

                if is_overload:
                    logger.warning(
                        "Gemini 503/overload on attempt %d — trying fallback model %s",
                        attempt + 1, _FALLBACK_MODEL,
                    )
                    try:
                        response = self._client.models.generate_content(
                            model=_FALLBACK_MODEL, contents=user, config=config,
                        )
                        return response.text
                    except Exception as fb_exc:
                        logger.warning("Fallback model %s also failed: %s", _FALLBACK_MODEL, fb_exc)
                        last_exc = fb_exc
                    raise LLMTransientError(
                        f"Gemini 503 and fallback {_FALLBACK_MODEL} both failed: {last_exc}"
                    ) from last_exc

                if is_exhausted:
                    # Per-minute rate limit — respect Google's retryDelay if present
                    if fallback_wait is None:
                        last_exc = exc
                        break
                    delay = _parse_retry_delay(exc) or fallback_wait
                    logger.warning(
                        "Gemini per-minute rate limit (attempt %d/%d) — sleeping %ds",
                        attempt + 1, len(_RATE_LIMIT_BACKOFF), delay,
                    )
                    time.sleep(delay)
                    last_exc = exc
                    continue

                raise  # auth errors, invalid requests, etc. — propagate immediately

        raise LLMTransientError(
            f"Gemini rate-limited after {len(_RATE_LIMIT_BACKOFF)} retries: {last_exc}"
        ) from last_exc
