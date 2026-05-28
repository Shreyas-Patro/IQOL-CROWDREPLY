from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .db import get_recent_posts, get_replies_for_post, get_scan_stats
from .pipeline import run_scan

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    posts = get_recent_posts(limit=50)
    posts_with_replies = []
    for post in posts:
        row = dict(post)
        row["replies"] = [dict(r) for r in get_replies_for_post(post["id"])]
        posts_with_replies.append(row)
    stats = get_scan_stats()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "posts": posts_with_replies, "stats": [dict(s) for s in stats]},
    )


@router.post("/scan")
async def trigger_scan():
    result = run_scan()
    return {"status": "ok", "result": result}


@router.get("/posts")
async def list_posts(limit: int = 50):
    return [dict(p) for p in get_recent_posts(limit=limit)]


@router.get("/posts/{post_id}/replies")
async def post_replies(post_id: str):
    rows = get_replies_for_post(post_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No replies found for this post")
    return [dict(r) for r in rows]
