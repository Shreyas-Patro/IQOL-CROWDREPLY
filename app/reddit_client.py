import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "iqol-crowdreply/1.0")
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

_client = httpx.Client(
    headers={"User-Agent": _USER_AGENT},
    follow_redirects=True,
)


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def fetch_subreddit_new(subreddit_name: str, limit: int = 50) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit_name}/new.json"

    def _get():
        return _client.get(url, params={"limit": limit}, timeout=20)

    try:
        resp = _get()
        if resp.status_code == 429:
            logger.warning("Rate limited on r/%s — retrying in 5s", subreddit_name)
            time.sleep(5)
            resp = _get()
        if resp.status_code in (403, 404):
            logger.warning("r/%s returned %s — skipping", subreddit_name, resp.status_code)
            return []
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Request failed for r/%s: %s", subreddit_name, exc)
        return []

    posts = []
    for child in resp.json().get("data", {}).get("children", []):
        if child.get("kind") != "t3":
            continue
        p = child["data"]
        posts.append({
            "id": p["id"],
            "subreddit": p["subreddit"],
            "title": p["title"],
            "body": p.get("selftext", "") or "",
            "author": p.get("author", "[deleted]"),
            "url": f"https://www.reddit.com{p['permalink']}",
            "posted_at": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc),
            "raw_json": json.dumps(p),
        })
    return posts


def matches_keywords(post: dict, keywords: list[str]) -> bool:
    text = (post.get("title", "") + " " + post.get("body", "")).lower()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            return True
    return False


def scan_subreddit(subreddit_name: str) -> list[dict]:
    config = _load_config()
    keywords = config.get("keywords", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    posts = fetch_subreddit_new(subreddit_name)
    return [
        p for p in posts
        if p["posted_at"] >= cutoff and matches_keywords(p, keywords)
    ]


def scan_all() -> list[dict]:
    config = _load_config()
    subreddits = config.get("subreddits", [])

    seen: set[str] = set()
    results: list[dict] = []

    for i, subreddit in enumerate(subreddits):
        if i > 0:
            time.sleep(2)
        try:
            logger.info("Scanning r/%s", subreddit)
            for post in scan_subreddit(subreddit):
                if post["id"] not in seen:
                    seen.add(post["id"])
                    results.append(post)
        except Exception as exc:
            logger.error("Failed scanning r/%s: %s", subreddit, exc)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    posts = scan_all()
    print(f"\nFound {len(posts)} matching posts\n")
    for post in posts[:3]:
        print(f"  {post['title']}")
        print(f"  {post['url']}\n")
