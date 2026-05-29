import re
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _build_kw_re(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    pattern = "|".join(r"\b" + re.escape(t) + r"\b" for t in terms)
    return re.compile(pattern, re.IGNORECASE)


_cfg = _load_config()
_AREAS = _cfg.get("areas_of_interest", [])

_RENTAL_RE = _build_kw_re(_cfg.get("rental_keywords", []))
_NEGATIVE_RE = _build_kw_re(_cfg.get("negative_keywords", []))

# Buy/sell intent only — no rental signals
_POSITIVE_INTENT = re.compile(
    r"\b(buy|for sale|selling|sell|purchase|resale|under construction"
    r"|ready to move|invest|investment|builder|new launch|freehold|prelaunch)\b",
    re.IGNORECASE,
)
_BHK_RE = re.compile(r"\d\s?bhk", re.IGNORECASE)
_BUDGET_RE = re.compile(r"\d+\s?(cr|crore|lakh|lac|l\b|k\b)", re.IGNORECASE)
_SQFT_RE = re.compile(r"\d+\s?sq\.?\s?ft", re.IGNORECASE)


def prefilter_score(title: str, body: str) -> int:
    text = (title + " " + (body or "")).lower()

    # Hard disqualifier: rental/PG intent dominates all positive signals
    if _RENTAL_RE and _RENTAL_RE.search(text):
        return 5

    score = 0
    if _POSITIVE_INTENT.search(text):
        score += 40
    if _BHK_RE.search(text):
        score += 20
    if _BUDGET_RE.search(text):
        score += 20
    if _SQFT_RE.search(text):
        score += 5
    for area in _AREAS:
        if area.lower() in text:
            score += 15
            break
    if _NEGATIVE_RE and _NEGATIVE_RE.search(text):
        score -= 40

    return max(0, min(100, score))


def passes_prefilter(title: str, body: str, threshold: int = 25) -> bool:
    return prefilter_score(title, body) >= threshold
