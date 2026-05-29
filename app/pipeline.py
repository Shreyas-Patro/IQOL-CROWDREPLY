import logging
import logging.handlers
import os
import time
from pathlib import Path

from .reddit_client import scan_all
from .llm_client import analyze_batch, analyze_one
from .llm import LLMTransientError, LLMDailyQuotaError
from .prefilter import passes_prefilter, prefilter_score as compute_prefilter_score
from .db import (
    get_post, get_posts, upsert_post, update_post_analysis,
    add_reply, delete_replies, update_status, set_dismiss_info,
)

MIN_SCORE = float(os.getenv("MIN_RELEVANCE_SCORE", "6"))
BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "5"))
CIRCUIT_BREAKER_THRESHOLD = 3

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


def _store_result(post_id: str, result: dict, pf_score: int = None) -> str:
    """Persist analysis result; returns 'qualified' or 'dismissed'."""
    score = float(result.get("score") or 0)
    update_post_analysis(
        post_id,
        score=score,
        intent=result.get("intent"),
        area=result.get("area"),
        bhk=result.get("bhk"),
        budget=result.get("budget"),
        urgency=result.get("urgency"),
    )
    if result.get("skip") or score < MIN_SCORE:
        reason = (
            "off-topic (LLM)" if result.get("skip")
            else f"low relevance (LLM score {score:.0f}/10)"
        )
        set_dismiss_info(post_id, prefilter_score=pf_score, dismiss_reason=reason)
        update_status(post_id, "dismissed")
        return "dismissed"
    if pf_score is not None:
        set_dismiss_info(post_id, prefilter_score=pf_score)
    # Promote to 'new' — handles both fresh posts and pending posts being retried
    update_status(post_id, "new")
    for r in result.get("replies", []):
        add_reply(post_id, tone=r.get("tone"), text=r.get("text", ""))
    return "qualified"


def run_scan_cycle() -> dict:
    # Re-queue any posts left pending from a previous cycle's circuit breaker
    pending_rows = get_posts(status="pending", limit=500)
    pending_survivors = [dict(r) for r in pending_rows]
    pending_requeued = len(pending_survivors)
    if pending_requeued:
        logger.info("Re-queuing %d pending posts from previous cycle", pending_requeued)

    posts = scan_all()
    scanned = len(posts)
    new_posts: list[dict] = []

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
                "source": post.get("source"),
            })
        except Exception as exc:
            logger.error("upsert_post failed for %s: %s", post["id"], exc)
            continue
        if is_new:
            new_posts.append(post)

    new = len(new_posts)
    prefiltered_out = qualified = dismissed = llm_calls_used = 0
    pending_left = 0
    circuit_broken = False
    daily_quota_hit = False

    # Pre-filter: dismiss without LLM call if clearly irrelevant
    fresh_survivors: list[dict] = []
    for post in new_posts:
        pf_score = compute_prefilter_score(post["title"], post.get("body") or "")
        post["_pf_score"] = pf_score
        if passes_prefilter(post["title"], post.get("body") or ""):
            fresh_survivors.append(post)
        else:
            set_dismiss_info(
                post["id"],
                prefilter_score=pf_score,
                dismiss_reason=f"pre-filter (score {pf_score})",
            )
            update_status(post["id"], "prefiltered")
            prefiltered_out += 1
            logger.debug("Pre-filtered %s (pf_score=%d)", post["id"], pf_score)

    # Combine pending retries with fresh survivors
    survivors = pending_survivors + fresh_survivors

    # Batch LLM analysis with circuit breaker
    consecutive_transient = 0
    for i in range(0, len(survivors), BATCH_SIZE):
        chunk = survivors[i : i + BATCH_SIZE]
        if i > 0:
            time.sleep(4)
        try:
            results = analyze_batch(chunk)
            consecutive_transient = 0
            llm_calls_used += 1
        except LLMTransientError as exc:
            consecutive_transient += 1
            logger.warning(
                "Transient LLM failure for chunk starting at %d (%d consecutive): %s",
                i, consecutive_transient, exc,
            )
            for post in chunk:
                update_status(post["id"], "pending")
            pending_left += len(chunk)

            if consecutive_transient >= CIRCUIT_BREAKER_THRESHOLD:
                remaining = survivors[i + BATCH_SIZE :]
                if remaining:
                    logger.warning(
                        "Circuit breaker tripped — leaving %d more posts as pending", len(remaining)
                    )
                    for post in remaining:
                        update_status(post["id"], "pending")
                    pending_left += len(remaining)
                circuit_broken = True
                break
            continue
        except LLMDailyQuotaError as exc:
            # Daily quota exhausted — retrying won't help until tomorrow; leave everything pending
            logger.warning(
                "Daily Gemini quota exhausted — aborting, resume tomorrow or switch provider"
            )
            remaining = survivors[i:]  # includes current chunk
            for post in remaining:
                update_status(post["id"], "pending")
            pending_left += len(remaining)
            circuit_broken = True
            daily_quota_hit = True
            break
        except RuntimeError as exc:
            # Budget exhausted — dismiss remaining
            remaining = survivors[i:]
            logger.warning("LLM budget exceeded: %s. Dismissing %d remaining posts.", exc, len(remaining))
            for post in remaining:
                update_status(post["id"], "dismissed")
            dismissed += len(remaining)
            break
        except Exception as exc:
            logger.error("analyze_batch failed for chunk %d: %s", i, exc)
            continue

        for post, result in zip(chunk, results):
            try:
                outcome = _store_result(post["id"], result, pf_score=post.get("_pf_score"))
            except Exception as exc:
                logger.error("_store_result failed for %s: %s", post["id"], exc)
                continue
            if outcome == "qualified":
                qualified += 1
                logger.info(
                    "Qualified %s — score=%.1f intent=%s area=%s",
                    post["id"], float(result.get("score") or 0),
                    result.get("intent"), result.get("area"),
                )
            else:
                dismissed += 1

    sources_breakdown: dict[str, int] = {}
    for post in posts:
        src = post.get("source") or "unknown"
        sources_breakdown[src] = sources_breakdown.get(src, 0) + 1

    summary = {
        "scanned": scanned,
        "new": new,
        "prefiltered_out": prefiltered_out,
        "llm_analyzed": len(survivors),
        "qualified": qualified,
        "dismissed": dismissed,
        "llm_calls_used": llm_calls_used,
        "sources_breakdown": sources_breakdown,
        "pending_requeued": pending_requeued,
        "pending_left": pending_left,
        "circuit_broken": circuit_broken,
        "daily_quota_hit": daily_quota_hit,
    }
    _scanner_logger.info(
        "cycle — scanned=%d new=%d prefiltered=%d qualified=%d dismissed=%d "
        "pending_requeued=%d pending_left=%d circuit_broken=%s daily_quota_hit=%s llm_calls=%d",
        scanned, new, prefiltered_out, qualified, dismissed,
        pending_requeued, pending_left, circuit_broken, daily_quota_hit, llm_calls_used,
    )
    from . import state
    state.record_scan()
    return summary


def regenerate_for_post(post_id: str) -> dict:
    post = get_post(post_id)
    if not post:
        raise ValueError(f"Post {post_id!r} not found in DB")

    result = analyze_one(post["title"], post["body"] or "")
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
