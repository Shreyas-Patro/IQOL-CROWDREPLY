import json
import logging
import logging.handlers
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_BRAND_NAME = os.getenv("BRAND_NAME", "AllDoors.in")
_BRAND_URL = os.getenv("BRAND_URL", "https://alldoors.in")
_DAILY_BUDGET = int(os.getenv("LLM_DAILY_BUDGET", "50"))

logger = logging.getLogger(__name__)

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
You are helping draft a Reddit comment that recommends {brand_name}, a Bangalore real estate platform for buying and selling property.

The comment must read like it was written by a real Reddit user who genuinely used the platform. NOT like a marketer.

Platform definition — read this before scoring:
{brand_name} is a BUY/SELL real estate platform ONLY. It helps people BUY properties for sale and SELL their property. It does NOT offer rentals, PG/hostel accommodation, leasing, or roommate matching.

Relevance scoring — apply these FIRST:
Score 6+ ONLY if the person is actively trying to:
 - BUY a flat, apartment, house, or plot, OR
 - SELL their property, OR
 - INVEST in property to purchase.

Score 0-3 and set "skip": true for:
 - Anyone looking to RENT, lease, or find a PG/hostel/roommate (we don't offer this)
 - Venting, advice, salary, or relationship posts that merely mention "property" or an area name
 - General city discussion with no buy/sell intent

Platform scope — when writing replies:
NEVER claim {brand_name} has features it doesn't. ONLY reference: browsing properties for sale, filtering by area/price/BHK/plot size, ready-to-move and resale listings, and listing your own property to sell. NEVER mention rentals, PGs, or any rental feature.

Reply rules — these are hard:
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

_SINGLE_SHAPE = (
    'Return ONLY a JSON object with this exact shape:\n'
    '{\n'
    '  "score": <0-10 integer, how relevant to a Bangalore real estate platform>,\n'
    '  "intent": "buy|rent|sell|invest|inquire",\n'
    '  "area": "<extracted area or null>",\n'
    '  "bhk": "<full label like 2BHK or 3BHK, or null>",\n'
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

_BATCH_SHAPE = (
    'Return ONLY a JSON array where each element has this exact shape.\n'
    'Each element MUST have a "replies" array with EXACTLY 3 objects — one per tone, in this order: fellow_buyer, helpful_local, experienced_user. Never fewer, never more.\n'
    '{\n'
    '  "score": <0-10 integer>,\n'
    '  "intent": "buy|rent|sell|invest|inquire",\n'
    '  "area": "<extracted area or null>",\n'
    '  "bhk": "<full label like 2BHK or 3BHK, or null>",\n'
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


def _get_provider():
    from .llm import get_provider
    return get_provider()


def _check_budget():
    from .db import count_llm_calls_today
    used = count_llm_calls_today()
    if used >= _DAILY_BUDGET:
        raise RuntimeError(f"Daily LLM budget of {_DAILY_BUDGET} calls reached ({used} used)")


def _parse_json(raw: str) -> dict | list:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    return json.loads(cleaned)


def analyze_one(title: str, body: str) -> dict:
    _check_budget()
    system = REPLY_GEN_SYSTEM_PROMPT.format(brand_name=_BRAND_NAME)
    user = (
        f"Title: {title}\n\n"
        f"Body: {body or '(no body)'}\n\n"
        + _SINGLE_SHAPE
    )
    try:
        raw = _get_provider().complete(system, user)
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return {"score": 0, "skip": True, "error": str(exc)}

    _llm_logger.info("analyze_one title=%s", title[:60])

    from .db import record_llm_call
    record_llm_call()

    try:
        result = _parse_json(raw)
    except json.JSONDecodeError:
        logger.error("JSON parse failed. Raw: %s", raw)
        return {"score": 0, "skip": True, "error": "parse failed", "raw": raw}

    return result


_BATCH_TRANSIENT_BACKOFF = (5, 15, 45)


def analyze_batch(posts: list[dict]) -> list[dict]:
    from .llm import LLMTransientError

    if not posts:
        return []

    _check_budget()

    system = REPLY_GEN_SYSTEM_PROMPT.format(brand_name=_BRAND_NAME)
    post_sections = "\n\n".join(
        f"Post {i}:\nTitle: {p['title']}\nBody: {p.get('body') or '(no body)'}"
        for i, p in enumerate(posts, 1)
    )
    user = (
        f"Analyse these {len(posts)} Reddit posts for a Bangalore real estate platform.\n\n"
        f"{post_sections}\n\n"
        f"Return exactly {len(posts)} objects in the same order. "
        "Each object must include exactly 3 reply variants (fellow_buyer, helpful_local, experienced_user).\n\n"
        + _BATCH_SHAPE
    )

    # Retry the SAME batch on transient failures — never split to per-post for transport errors
    raw = None
    last_transient_exc = None
    for attempt, wait in enumerate((*_BATCH_TRANSIENT_BACKOFF, None)):
        try:
            raw = _get_provider().complete(system, user)
            last_transient_exc = None
            break
        except LLMTransientError as exc:
            last_transient_exc = exc
            if wait is not None:
                logger.warning(
                    "Batch transient failure (attempt %d/%d) — sleeping %ds: %s",
                    attempt + 1, len(_BATCH_TRANSIENT_BACKOFF) + 1, wait, exc,
                )
                time.sleep(wait)

    if last_transient_exc is not None:
        raise last_transient_exc

    _llm_logger.info("analyze_batch n=%d", len(posts))

    from .db import record_llm_call
    record_llm_call()

    try:
        results = _parse_json(raw)
        if not isinstance(results, list):
            raise ValueError("response is not a JSON array")
        if len(results) != len(posts):
            raise ValueError(f"expected {len(posts)} items, got {len(results)}")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Batch parse failed (%s) — falling back to per-post. Raw: %.300s", exc, raw)
        return [analyze_one(p["title"], p.get("body") or "") for p in posts]

    return results


# Backward-compatible alias used by regenerate_for_post
analyze_and_generate = analyze_one


if __name__ == "__main__":
    import pprint
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    test_posts = [
        {
            "title": "Looking for a 2bhk for sale in Koramangala under 1.5cr. Any leads? Tired of brokers.",
            "body": "",
        },
        {
            "title": "Best salons in Indiranagar — recommendations?",
            "body": "Looking for a good hair salon near 100 feet road.",
        },
        {
            "title": "Sarjapur Road plot for investment — 1200sqft under 60L",
            "body": "Any DTCP approved layouts around Sarjapur that are still reasonably priced?",
        },
    ]

    print("=== analyze_batch (3 posts) ===")
    results = analyze_batch(test_posts)
    for i, (post, res) in enumerate(zip(test_posts, results), 1):
        print(f"\nPost {i}: {post['title'][:60]}")
        print(f"  score={res.get('score')}  skip={res.get('skip')}  intent={res.get('intent')}")
    pprint.pp(results)
