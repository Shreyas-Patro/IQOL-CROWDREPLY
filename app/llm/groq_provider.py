import logging
import os
import time

from .base import LLMProvider, LLMTransientError, LLMDailyQuotaError

logger = logging.getLogger(__name__)
_llm_logger = logging.getLogger("llm_calls")

_RATE_LIMIT_BACKOFF = (5, 10, 20)


def _is_daily_quota(exc) -> bool:
    msg = str(exc).lower()
    return "per day" in msg or "per_day" in msg or ("daily" in msg and "quota" in msg)


def _parse_retry_after(exc) -> int | None:
    try:
        val = exc.response.headers.get("retry-after")
        if val:
            return min(int(float(val)), 120)
    except Exception:
        pass
    return None


class GroqProvider(LLMProvider):
    def complete(self, system: str, user: str) -> str:
        from groq import Groq
        from groq import APIStatusError

        api_key = os.getenv("GROQ_API_KEY", "")
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

        client = Groq(api_key=api_key)

        # JSON mode requires the word "JSON" in the prompt
        user_msg = user if "json" in user.lower() else user + "\nRespond with valid JSON."

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

        last_exc = None
        for attempt, fallback_wait in enumerate((*_RATE_LIMIT_BACKOFF, None)):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1500,
                    response_format={"type": "json_object"},
                )
                usage = response.usage
                _llm_logger.info(
                    "groq model=%s in=%d out=%d",
                    model,
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                )
                return response.choices[0].message.content

            except APIStatusError as exc:
                if exc.status_code == 429:
                    if _is_daily_quota(exc):
                        raise LLMDailyQuotaError(
                            "Daily Groq quota exhausted — aborting, resume tomorrow or switch provider"
                        ) from exc
                    # Per-minute rate limit
                    if fallback_wait is None:
                        last_exc = exc
                        break
                    delay = _parse_retry_after(exc) or fallback_wait
                    logger.warning(
                        "Groq rate limit (attempt %d/%d) — sleeping %ds: %s",
                        attempt + 1, len(_RATE_LIMIT_BACKOFF), delay, exc,
                    )
                    time.sleep(delay)
                    last_exc = exc
                    continue

                if exc.status_code >= 500:
                    raise LLMTransientError(
                        f"Groq server error ({exc.status_code}): {exc}"
                    ) from exc

                raise  # 4xx other than 429 (auth, bad request) — propagate immediately

        raise LLMTransientError(
            f"Groq rate-limited after {len(_RATE_LIMIT_BACKOFF)} retries: {last_exc}"
        ) from last_exc


if __name__ == "__main__":
    import logging
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = GroqProvider()
    result = p.complete("You are helpful.", 'Reply with JSON: {"ok": true}')
    print(result)
