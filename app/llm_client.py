import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
FALLBACK_MODEL = os.getenv("OPENROUTER_FALLBACK_MODEL", "google/gemini-2.0-flash-exp:free")
BRAND_NAME = os.getenv("BRAND_NAME", "AllDoors.in")
BRAND_URL = os.getenv("BRAND_URL", "https://alldoors.in")


def _chat(messages: list[dict], model: str) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": BRAND_URL,
        "X-Title": BRAND_NAME,
    }
    payload = {"model": model, "messages": messages, "max_tokens": 512}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{OPENROUTER_BASE}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _chat_with_fallback(messages: list[dict]) -> str:
    try:
        return _chat(messages, MODEL)
    except Exception as e:
        logger.warning(f"Primary model failed ({e}), trying fallback")
        return _chat(messages, FALLBACK_MODEL)


def score_relevance(title: str, body: str) -> int:
    """Return 1-10 relevance score for real estate outreach in Bangalore."""
    prompt = (
        f"You are a relevance scorer for {BRAND_NAME}, a real estate platform in Bangalore.\n"
        f"Score 1-10 how relevant this Reddit post is for someone actively looking to "
        f"buy or rent property in Bangalore. Reply with a single integer only.\n\n"
        f"Title: {title}\nBody: {body[:500]}"
    )
    try:
        result = _chat_with_fallback([{"role": "user", "content": prompt}])
        return max(1, min(10, int(result.strip().split()[0])))
    except Exception as e:
        logger.error(f"Scoring failed: {e}")
        return 0


def generate_reply(title: str, body: str) -> str:
    """Draft a helpful, non-spammy Reddit reply that naturally mentions the brand."""
    prompt = (
        f"You are a helpful community member who works at {BRAND_NAME} ({BRAND_URL}), "
        f"a real estate platform in Bangalore.\n"
        f"Write a genuine, conversational Reddit reply to the post below.\n"
        f"Rules:\n"
        f"- Be helpful first; mention {BRAND_NAME} only when naturally relevant\n"
        f"- No hard selling or spammy phrases\n"
        f"- Under 150 words\n"
        f"- Plain text, no markdown headers\n\n"
        f"Title: {title}\nBody: {body[:800]}"
    )
    try:
        return _chat_with_fallback([{"role": "user", "content": prompt}])
    except Exception as e:
        logger.error(f"Reply generation failed: {e}")
        return ""
