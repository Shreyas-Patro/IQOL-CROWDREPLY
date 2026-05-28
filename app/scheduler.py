import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
_scheduler = BackgroundScheduler(daemon=True)


def start_scheduler():
    from .pipeline import run_pipeline  # late import avoids circular deps

    _scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(minutes=INTERVAL_MINUTES),
        id="reddit_scan",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    _scheduler.start()
    logger.info("Scheduler started — scanning every %d min", INTERVAL_MINUTES)


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
