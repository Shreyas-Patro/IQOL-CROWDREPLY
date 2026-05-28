import logging
import os

from .reddit_client import scan_all
from .llm_client import analyze_and_generate
from .db import upsert_post, update_post_analysis, add_reply, update_status

logger = logging.getLogger(__name__)
MIN_SCORE = float(os.getenv("MIN_RELEVANCE_SCORE", "6"))


def run_pipeline() -> dict:
    posts = scan_all()
    logger.info("Scan returned %d keyword-matched posts", len(posts))

    new_count = 0
    analyzed_count = 0
    dismissed_count = 0

    for post in posts:
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
        if not is_new:
            continue
        new_count += 1

        result = analyze_and_generate(post["title"], post["body"])

        if result.get("skip") or float(result.get("score") or 0) < MIN_SCORE:
            update_status(post["id"], "dismissed")
            dismissed_count += 1
            logger.debug(
                "Dismissed %s (score=%s skip=%s)", post["id"],
                result.get("score"), result.get("skip"),
            )
            continue

        update_post_analysis(
            post["id"],
            score=float(result.get("score", 0)),
            intent=result.get("intent"),
            area=result.get("area"),
            bhk=result.get("bhk"),
            budget=result.get("budget"),
            urgency=result.get("urgency"),
        )
        for r in result.get("replies", []):
            add_reply(post["id"], tone=r.get("tone"), text=r.get("text", ""))
        analyzed_count += 1
        logger.info(
            "Analyzed %s — score=%s intent=%s area=%s",
            post["id"], result.get("score"), result.get("intent"), result.get("area"),
        )

    return {
        "posts_found": len(posts),
        "new": new_count,
        "analyzed": analyzed_count,
        "dismissed": dismissed_count,
    }
