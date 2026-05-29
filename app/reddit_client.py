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


def _parse_listing(resp_json: dict) -> list[dict]:
    posts = []
    for child in resp_json.get("data", {}).get("children", []):
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

    return _parse_listing(resp.json())


def search_reddit(
    query: str,
    subreddit: str = None,
    sort: str = "new",
    limit: int = 50,
) -> list[dict]:
    if subreddit:
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {"q": query, "restrict_sr": "1", "sort": sort, "limit": limit}
    else:
        url = "https://www.reddit.com/search.json"
        params = {"q": query, "sort": sort, "limit": limit}

    label = f"r/{subreddit}: {query[:40]}" if subreddit else f"site-wide: {query[:40]}"

    def _get():
        return _client.get(url, params=params, timeout=20)

    try:
        resp = _get()
        if resp.status_code == 429:
            logger.warning("Rate limited on search '%s' — retrying in 5s", label)
            time.sleep(5)
            resp = _get()
        if resp.status_code in (403, 404):
            logger.warning("Search '%s' returned %s — skipping", label, resp.status_code)
            return []
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Search request failed for '%s': %s", label, exc)
        return []

    return _parse_listing(resp.json())


def matches_keywords(post: dict, keywords: list[str]) -> bool:
    text = (post.get("title", "") + " " + post.get("body", "")).lower()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            return True
    return False


def scan_all() -> list[dict]:
    config = _load_config()
    subreddits = config.get("subreddits", [])
    keywords = config.get("keywords", [])
    search_queries = config.get("search_queries", [])
    settings = config.get("search_settings", {})

    site_wide = settings.get("site_wide", True)
    scoped = settings.get("scoped_to_core_subs", True)
    sort = settings.get("sort", "new")
    limit = int(settings.get("limit", 50))
    max_age_days = int(settings.get("max_age_days", 30))

    feed_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    search_cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    seen: set[str] = set()
    results: list[dict] = []
    request_count = 0

    def _sleep():
        nonlocal request_count
        if request_count > 0:
            time.sleep(2)
        request_count += 1

    def _add(post: dict, source: str) -> bool:
        if post["id"] in seen:
            return False
        seen.add(post["id"])
        post["source"] = source
        results.append(post)
        return True

    # (a) /new feeds — keyword-filtered, 7-day cutoff
    for subreddit in subreddits:
        _sleep()
        try:
            logger.info("Scanning r/%s /new", subreddit)
            for p in fetch_subreddit_new(subreddit, limit=50):
                if p["posted_at"] >= feed_cutoff and matches_keywords(p, keywords):
                    _add(p, "new_feed")
        except Exception as exc:
            logger.error("Failed r/%s /new: %s", subreddit, exc)

    # (b) Site-wide search — intent-focused queries, max_age_days cutoff
    if site_wide and search_queries:
        for query in search_queries:
            _sleep()
            try:
                logger.info("Searching site-wide: %s", query)
                for p in search_reddit(query, sort=sort, limit=limit):
                    if p["posted_at"] >= search_cutoff:
                        _add(p, "search_sitewide")
            except Exception as exc:
                logger.error("Failed site-wide search '%s': %s", query[:40], exc)

    # (c) Scoped search — each query restricted to each core subreddit
    if scoped and search_queries:
        for query in search_queries:
            for subreddit in subreddits:
                _sleep()
                try:
                    logger.info("Searching r/%s: %s", subreddit, query)
                    for p in search_reddit(query, subreddit=subreddit, sort=sort, limit=limit):
                        if p["posted_at"] >= search_cutoff:
                            _add(p, "search_scoped")
                except Exception as exc:
                    logger.error("Failed scoped search r/%s '%s': %s", subreddit, query[:40], exc)

    logger.info(
        "scan_all complete — %d unique posts from %d requests (%d new_feed / %d sitewide / %d scoped)",
        len(results), request_count,
        sum(1 for p in results if p.get("source") == "new_feed"),
        sum(1 for p in results if p.get("source") == "search_sitewide"),
        sum(1 for p in results if p.get("source") == "search_scoped"),
    )
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    posts = scan_all()
    from collections import Counter
    sources = Counter(p.get("source") for p in posts)
    print(f"\nFound {len(posts)} unique posts — {dict(sources)}")
    for post in posts[:5]:
        print(f"  [{post.get('source')}] {post['title'][:70]}")
        print(f"  {post['url']}\n")
