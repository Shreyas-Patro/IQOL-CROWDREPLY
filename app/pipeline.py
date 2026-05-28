import logging
import logging.handlers
import os
from pathlib import Path

from .reddit_client import scan_all
from .llm_client import analyze_and_generate
from .db import (
    get_post, upsert_post, update_post_analysis,
    add_reply, delete_replies, update_status,
)

MIN_SCORE = float(os.getenv("MIN_RELEVANCE_SCORE", "6"))

logger = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

_scanner_logger = logging.getLogger("scanner")
if not _scanner_logger.handlers:
    _fh = logging.FileHandler(_LOGS_DIR / "scanner.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _scanner_logger.addHandler(_fh)
    _scanner_logger.setLevel(logging.INFO)
    _scanner_logger.propagate = False


def process_post(post_dict: dict) -> dict:
    post_id = post_dict["id"]
    result = analyze_and_generate(post_dict["title"], post_dict.get("body") or "")

    score = float(result.get("score") or 0)

    if result.get("skip") or score < MIN_SCORE:
        # Save score for stats even when dismissing
        update_post_analysis(
            post_id,
            score=score,
            intent=result.get("intent"),
            area=result.get("area"),
            bhk=result.get("bhk"),
            budget=result.get("budget"),
            urgency=result.get("urgency"),
        )
        update_status(post_id, "dismissed")
        logger.debug("Dismissed %s (score=%.1f skip=%s)", post_id, score, result.get("skip"))
        return {"action": "dismissed", "post_id": post_id}

    update_post_analysis(
        post_id,
        score=score,
        intent=result.get("intent"),
        area=result.get("area"),
        bhk=result.get("bhk"),
        budget=result.get("budget"),
        urgency=result.get("urgency"),
    )
    for r in result.get("replies", []):
        add_reply(post_id, tone=r.get("tone"), text=r.get("text", ""))

    logger.info(
        "Qualified %s — score=%.1f intent=%s area=%s",
        post_id, score, result.get("intent"), result.get("area"),
    )
    return {"action": "qualified", "post_id": post_id, "score": score}


def run_scan_cycle() -> dict:
    posts = scan_all()
    scanned = len(posts)
    new = qualified = dismissed = 0

    for post in posts:
        try:
            is_new = upsert_post({
                "id": post["id"],
                "subreddit": post["subreddit"],
                "title": post["title"],
                "body": post["body"],
                "author": post["author"],
                "url": post["url"],
                "posted_at": post["posted_at"].isoformat(),
                "score": None,
                "raw_json": post["raw_json"],
            })
        except Exception as exc:
            logger.error("upsert_post failed for %s: %s", post["id"], exc)
            continue

        if not is_new:
            continue
        new += 1

        try:
            outcome = process_post(post)
        except Exception as exc:
            logger.error("process_post failed for %s: %s", post["id"], exc)
            continue

        if outcome["action"] == "qualified":
            qualified += 1
        else:
            dismissed += 1

    summary = {"scanned": scanned, "new": new, "qualified": qualified, "dismissed": dismissed}
    _scanner_logger.info(
        "cycle complete — scanned=%d new=%d qualified=%d dismissed=%d",
        scanned, new, qualified, dismissed,
    )
    return summary


def regenerate_for_post(post_id: str) -> dict:
    post = get_post(post_id)
    if not post:
        raise ValueError(f"Post {post_id!r} not found in DB")

    result = analyze_and_generate(post["title"], post["body"] or "")
    if result.get("error"):
        raise RuntimeError(result["error"])

    delete_replies(post_id)
    update_post_analysis(
        post_id,
        score=float(result.get("score") or 0),
        intent=result.get("intent"),
        area=result.get("area"),
        bhk=result.get("bhk"),
        budget=result.get("budget"),
        urgency=result.get("urgency"),
    )
    for r in result.get("replies", []):
        add_reply(post_id, tone=r.get("tone"), text=r.get("text", ""))

    logger.info("Regenerated replies for %s", post_id)
    return result
