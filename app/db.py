import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "iqol.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                subreddit TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                author TEXT,
                url TEXT NOT NULL,
                posted_at TIMESTAMP NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                score REAL,
                intent TEXT,
                area TEXT,
                bhk TEXT,
                budget TEXT,
                urgency TEXT,
                status TEXT DEFAULT 'new',
                reply_used TEXT,
                replied_at TIMESTAMP,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT REFERENCES posts(id) ON DELETE CASCADE,
                tone TEXT,
                text TEXT NOT NULL,
                edited_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_posts_posted ON posts(posted_at DESC);
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_post(post_dict: dict) -> bool:
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM posts WHERE id = ?", (post_dict["id"],)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """INSERT INTO posts
               (id, subreddit, title, body, author, url, posted_at, score, raw_json)
               VALUES (:id, :subreddit, :title, :body, :author, :url, :posted_at, :score, :raw_json)""",
            post_dict,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def update_post_analysis(post_id: str, score: float, intent: str, area: str, bhk: str, budget: str, urgency: str):
    conn = get_conn()
    try:
        conn.execute(
            """UPDATE posts
               SET score=?, intent=?, area=?, bhk=?, budget=?, urgency=?
               WHERE id=?""",
            (score, intent, area, bhk, budget, urgency, post_id),
        )
        conn.commit()
    finally:
        conn.close()


def add_reply(post_id: str, tone: str, text: str):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO replies (post_id, tone, text) VALUES (?, ?, ?)",
            (post_id, tone, text),
        )
        conn.commit()
    finally:
        conn.close()


def delete_replies(post_id: str):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM replies WHERE post_id = ?", (post_id,))
        conn.commit()
    finally:
        conn.close()


def update_status(post_id: str, status: str, reply_used: str = None):
    conn = get_conn()
    try:
        if status == "replied":
            replied_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE posts SET status=?, reply_used=?, replied_at=? WHERE id=?",
                (status, reply_used, replied_at, post_id),
            )
        else:
            conn.execute(
                "UPDATE posts SET status=?, reply_used=? WHERE id=?",
                (status, reply_used, post_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_posts(status: str = None, min_score: float = None, subreddit: str = None, limit: int = 100) -> list:
    conn = get_conn()
    try:
        clauses = []
        params = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        if subreddit is not None:
            clauses.append("subreddit = ?")
            params.append(subreddit)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return conn.execute(
            f"SELECT * FROM posts {where} ORDER BY posted_at DESC LIMIT ?", params
        ).fetchall()
    finally:
        conn.close()


def get_post(post_id: str):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    finally:
        conn.close()


def get_replies(post_id: str) -> list:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM replies WHERE post_id = ? ORDER BY created_at ASC",
            (post_id,),
        ).fetchall()
    finally:
        conn.close()


def get_stats() -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM posts GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        replied_this_week = conn.execute(
            """SELECT COUNT(*) FROM posts
               WHERE status = 'replied'
               AND replied_at >= datetime('now', '-7 days')"""
        ).fetchone()[0]
        return {
            "new": counts.get("new", 0),
            "reviewed": counts.get("reviewed", 0),
            "replied": counts.get("replied", 0),
            "dismissed": counts.get("dismissed", 0),
            "replied_this_week": replied_this_week,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {DB_PATH.resolve()}")
