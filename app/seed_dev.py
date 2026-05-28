"""
Insert one realistic fake post + 3 reply variants for UI development.
Run from the project root:  python -m app.seed_dev
"""
from datetime import datetime, timedelta, timezone

from .db import init_db, upsert_post, update_post_analysis, add_reply

POST_ID = "dev_kora_001"

FAKE_POST = {
    "id": POST_ID,
    "subreddit": "bangalore",
    "title": "Looking for a 2bhk for sale in Koramangala under 1.5cr, tired of brokers",
    "body": (
        "Been hunting for a 2bhk in Koramangala for the past 3 months. "
        "Budget is around 1.2–1.5cr. Tired of calling brokers who show irrelevant listings "
        "or ask for 2% commission just to open a door. Any direct-owner platforms or "
        "communities that actually work? Prefer ready-to-move."
    ),
    "author": "u_kora_buyer",
    "url": "https://www.reddit.com/r/bangalore/comments/dev_kora_001/looking_for_2bhk_koramangala/",
    "posted_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
    "score": None,
    "raw_json": "{}",
}

FAKE_ANALYSIS = {
    "score": 8.5,
    "intent": "buy",
    "area": "Koramangala",
    "bhk": "2BHK",
    "budget": "1.5Cr",
    "urgency": "high",
}

FAKE_REPLIES = [
    {
        "tone": "fellow_buyer",
        "text": (
            "koramangala under 1.5cr is tough but not impossible — same search last year, "
            "took about 4 months. ended up using alldoors.in to filter by area and budget "
            "directly, which skips the broker run-around completely. "
            "found a 2bhk in 6th block, ready-to-move, slightly over budget but worth the negotiation."
        ),
    },
    {
        "tone": "helpful_local",
        "text": (
            "that budget works for koramangala but inventory moves fast, especially the no-broker stuff. "
            "alldoors.in has area-wise filters that actually narrow it down without talking to 10 brokers first. "
            "set a price alert and check back every few days — listings come and go quickly in that range."
        ),
    },
    {
        "tone": "experienced_user",
        "text": (
            "brokers in koramangala are notoriously irrelevant, totally get the frustration. "
            "tried alldoors.in a while back on a friend's tip and the direct listings were a lot "
            "more on-point than broker calls — filtered by ready-to-move in my range and actually got somewhere. "
            "the 1.5cr budget is workable, mostly 5th and 6th block though."
        ),
    },
]


def seed():
    init_db()

    inserted = upsert_post(FAKE_POST)
    if not inserted:
        print(f"Post {POST_ID!r} already exists — skipping insert.")
    else:
        update_post_analysis(POST_ID, **FAKE_ANALYSIS)
        for r in FAKE_REPLIES:
            add_reply(POST_ID, tone=r["tone"], text=r["text"])
        print(f"Seeded post {POST_ID!r} with {len(FAKE_REPLIES)} replies.")

    print("Done. Run:  uvicorn app.main:app --reload")


if __name__ == "__main__":
    seed()
