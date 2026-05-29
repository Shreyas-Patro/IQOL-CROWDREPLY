import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import state
from .db import get_post, get_posts, get_replies, get_stats, update_status

router = APIRouter()
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

_MIN_SCORE_ENV = float(os.getenv("MIN_RELEVANCE_SCORE", "6"))
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_subreddits() -> list[str]:
    try:
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("subreddits", [])
    except Exception:
        return []


def _timeago(value) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - value
        if delta.days >= 1:
            return f"{delta.days}d ago"
        h = delta.seconds // 3600
        if h >= 1:
            return f"{h}h ago"
        m = delta.seconds // 60
        return f"{m}m ago"
    except Exception:
        return str(value)[:16]


templates.env.filters["timeago"] = _timeago


def _post_with_replies(post_id: str) -> dict | None:
    row = get_post(post_id)
    if not row:
        return None
    p = dict(row)
    p["replies"] = [dict(r) for r in get_replies(p["id"])]
    return p


# ── HTML ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    status: Optional[str] = "new",
    min_score: Optional[float] = None,
    subreddit: Optional[str] = None,
    source: Optional[str] = None,
):
    filter_status = None if not status or status == "all" else status
    _audit_view = filter_status in (None, "dismissed", "prefiltered", "pending")
    # Audit views show everything — never apply a default floor.
    # Actionable views (new / reviewed / replied) apply env default when slider not set.
    if _audit_view:
        effective_min_score = None
    else:
        effective_min_score = min_score if min_score is not None else _MIN_SCORE_ENV

    rows = get_posts(
        status=filter_status,
        min_score=effective_min_score,
        subreddit=subreddit,
        source=source if source and source != "all" else None,
        limit=100,
    )
    posts = []
    for row in rows:
        p = dict(row)
        p["replies"] = [dict(r) for r in get_replies(p["id"])]
        posts.append(p)

    return templates.TemplateResponse(request, "dashboard.html", {
        "posts": posts,
        "stats": get_stats(),
        "filters": {
            "status": status or "new",
            "min_score": effective_min_score,
            "subreddit": subreddit,
            "source": source or "all",
            "audit_view": _audit_view,
        },
        "subreddits": _load_subreddits(),
        "last_scan_time": state.get_last_scan(),
    })


# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/api/posts")
async def api_posts(
    status: Optional[str] = None,
    min_score: Optional[float] = None,
    subreddit: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 100,
):
    rows = get_posts(status=status, min_score=min_score, subreddit=subreddit, source=source, limit=limit)
    result = []
    for row in rows:
        p = dict(row)
        p["replies"] = [dict(r) for r in get_replies(p["id"])]
        result.append(p)
    return result


class StatusUpdate(BaseModel):
    status: str
    reply_used: Optional[str] = None


@router.post("/api/posts/{post_id}/status")
async def set_post_status(post_id: str, body: StatusUpdate):
    if not get_post(post_id):
        raise HTTPException(status_code=404, detail="Post not found")
    update_status(post_id, body.status, reply_used=body.reply_used)
    return _post_with_replies(post_id)


@router.post("/api/posts/{post_id}/regenerate")
async def regenerate_replies(post_id: str, request: Request):
    if not get_post(post_id):
        raise HTTPException(status_code=404, detail="Post not found")
    try:
        from .pipeline import regenerate_for_post
        regenerate_for_post(post_id)
        post = _post_with_replies(post_id)
        # HTMX request: return HTML card partial for outerHTML swap
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                request, "_post_card.html", {"post": post}
            )
        return post
    except Exception as exc:
        logger.error("Regenerate failed for %s: %s", post_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/scan")
async def trigger_scan():
    try:
        from .pipeline import run_scan_cycle
        return run_scan_cycle()
    except Exception as exc:
        logger.error("Scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/stats")
async def api_stats():
    return get_stats()
