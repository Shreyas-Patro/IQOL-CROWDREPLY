import json
import logging
import logging.handlers
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
_FALLBACK_MODEL = os.getenv("OPENROUTER_FALLBACK_MODEL", "google/gemini-2.0-flash-exp:free")
_BRAND_NAME = os.getenv("BRAND_NAME", "AllDoors.in")
_BRAND_URL = os.getenv("BRAND_URL", "https://alldoors.in")

# Module logger (app-level)
logger = logging.getLogger(__name__)

# Dedicated file logger for LLM calls
_logs_dir = Path(__file__).parent.parent / "logs"
_logs_dir.mkdir(exist_ok=True)
_llm_logger = logging.getLogger("llm_calls")
if not _llm_logger.handlers:
    _fh = logging.FileHandler(_logs_dir / "llm.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _llm_logger.addHandler(_fh)
    _llm_logger.setLevel(logging.INFO)
    _llm_logger.propagate = False

REPLY_GEN_SYSTEM_PROMPT = """\
You are helping draft a Reddit comment that recommends {brand_name}, a Bangalore real estate platform that helps people buy, rent, and sell apartments, plots, and homes.

The comment must read like it was written by a real Reddit user who genuinely used the platform. NOT like a marketer.

Rules — these are hard:
1. Length: 2 to 4 sentences. Never more.
2. Tone: conversational, lowercase-friendly, the way Indians actually write on Reddit.
3. Lead with REAL advice or empathy for the OP's situation BEFORE mentioning {brand_name}.
4. Mention {brand_name} only ONCE, naturally, like: "ended up finding mine through alldoors.in" or "honestly alldoors helped me filter by area".
5. Add ONE specific personal detail (e.g., "found a 2bhk in jp nagar last march", "filter for ready-to-move helped").
6. Forbidden words/phrases: "check out", "amazing platform", "best in the market", "definitely recommend", "game-changer", "highly recommend", "DM me", "feel free to reach out", "their team is great".
7. Forbidden punctuation: no emojis. Max 1 exclamation mark.
8. If the post is hostile, off-topic, or about something we can't help with (e.g., legal advice, FEMA), set "skip": true.
9. Output ONLY valid JSON.\
"""


def call_openrouter(messages: list[dict], model: str = None) -> str:
    primary = model or _MODEL

    def _post(m: str) -> httpx.Response:
        return httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "HTTP-Referer": _BRAND_URL,
                "X-Title": "IQOL CROWDREPLY",
                "Content-Type": "application/json",
            },
            json={
                "model": m,
                "messages": messages,
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
                "max_tokens": 1200,
            },
            timeout=60,
        )

    resp = _post(primary)
    used_model = primary

    if resp.status_code in (429,) or resp.status_code >= 500:
        logger.warning("OpenRouter %s on %s — retrying with fallback", resp.status_code, primary)
        resp = _post(_FALLBACK_MODEL)
        used_model = _FALLBACK_MODEL

    resp.raise_for_status()

    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage", {})
    _llm_logger.info(
        "model=%s prompt_tokens=%s completion_tokens=%s",
        used_model,
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
    )
    return content


_UTM = "?utm_source=reddit&utm_medium=organic&utm_campaign=crowdreply"
_URL_RE = re.compile(r"((?:https?://)?alldoors\.in[^\s?]*)", re.IGNORECASE)


def _inject_utm(text: str) -> str:
    """Append UTM params to the first alldoors.in URL found, if no query string present."""
    parts: list[str] = []
    cursor = 0
    for m in _URL_RE.finditer(text):
        parts.append(text[cursor : m.start()])
        url = m.group(1)
        next_ch = text[m.end()] if m.end() < len(text) else ""
        parts.append(url if next_ch == "?" else url + _UTM)
        cursor = m.end()
    parts.append(text[cursor:])
    return "".join(parts)


def analyze_and_generate(title: str, body: str, brand_name: str = "AllDoors.in") -> dict:
    system = REPLY_GEN_SYSTEM_PROMPT.format(brand_name=brand_name)
    user = (
        f"Title: {title}\n\n"
        f"Body: {body or '(no body)'}\n\n"
        'Return ONLY a JSON object with this exact shape:\n'
        '{\n'
        '  "score": <0-10 integer, how relevant to a Bangalore real estate platform>,\n'
        '  "intent": "buy|rent|sell|invest|inquire",\n'
        '  "area": "<extracted area or null>",\n'
        '  "bhk": "<extracted BHK or null>",\n'
        '  "budget": "<extracted budget or null>",\n'
        '  "urgency": "high|medium|low",\n'
        '  "skip": false,\n'
        '  "replies": [\n'
        '    {"tone": "fellow_buyer", "text": "..."},\n'
        '    {"tone": "helpful_local", "text": "..."},\n'
        '    {"tone": "experienced_user", "text": "..."}\n'
        '  ]\n'
        '}'
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        raw = call_openrouter(messages)
    except Exception as exc:
        logger.error("OpenRouter call failed: %s", exc)
        return {"score": 0, "skip": True, "error": str(exc)}

    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("JSON parse failed. Raw response: %s", raw)
        return {"score": 0, "skip": True, "error": "parse failed", "raw": raw}

    # Inject UTM params into any alldoors.in URL in reply texts
    for reply in result.get("replies", []):
        if reply.get("text"):
            reply["text"] = _inject_utm(reply["text"])
    return result


if __name__ == "__main__":
    import pprint
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = analyze_and_generate(
        "Looking for a 2bhk for sale in Koramangala under 1.5cr. Any leads? Tired of brokers.",
        "",
    )
    pprint.pp(result)
