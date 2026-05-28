import os
import logging
import yaml
from pathlib import Path

from .reddit_client import fetch_new_posts, search_subreddit
from .llm_client import score_relevance, generate_reply
from .db import upsert_post, save_reply, log_scan

logger = logging.getLogger(__name__)
MIN_SCORE = int(os.getenv("MIN_RELEVANCE_SCORE", "6"))


def load_config() -> dict:
    with open(Path("config.yaml")) as f:
        return yaml.safe_load(f)


def run_scan() -> dict:
    """Full pipeline: scan Reddit → score → generate replies for relevant posts."""
    config = load_config()
    subreddits: list[str] = config.get("subreddits", [])
    keywords: list[str] = config.get("keywords", [])

    total_found = 0
    total_relevant = 0

    for subreddit in subreddits:
        raw_posts: list[dict] = []

        raw_posts.extend(fetch_new_posts(subreddit))

        # Search a rotating subset of keywords to avoid hammering the API
        for kw in keywords[:4]:
            raw_posts.extend(search_subreddit(subreddit, kw))

        # Deduplicate within this batch
        seen: set[str] = set()
        unique: list[dict] = []
        for p in raw_posts:
            if p["id"] not in seen:
                seen.add(p["id"])
                unique.append(p)

        total_found += len(unique)
        relevant_count = 0

        for post in unique:
            combined = f"{post['title']} {post['body']}".strip()
            if not combined:
                continue

            post["relevance_score"] = score_relevance(post["title"], post["body"])
            is_new = upsert_post(post)

            if not is_new:
                continue

            if post["relevance_score"] >= MIN_SCORE:
                reply = generate_reply(post["title"], post["body"])
                if reply:
                    save_reply(post["id"], reply)
                    relevant_count += 1

        log_scan(subreddit, len(unique), relevant_count)
        total_relevant += relevant_count
        logger.info(f"r/{subreddit}: {len(unique)} posts scanned, {relevant_count} replies drafted")

    return {
        "subreddits_scanned": len(subreddits),
        "posts_found": total_found,
        "replies_generated": total_relevant,
    }
