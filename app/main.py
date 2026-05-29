import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import create_user, get_user_by_email, init_db
from .routes import router
from .scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


def _bootstrap_admin():
    email = os.getenv("ADMIN_EMAIL", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    name = os.getenv("ADMIN_NAME", "Admin")
    if not email or not password:
        logger.warning("ADMIN_EMAIL or ADMIN_PASSWORD not set — skipping user bootstrap")
        return
    if get_user_by_email(email):
        logger.info("Bootstrap user ready: %s", email)
        return
    create_user(email, password, name)
    logger.info("Bootstrap user ready: %s", email)

    # Future users — uncomment + add to env when needed:
    # create_user(settings.user2_email, settings.user2_password, settings.user2_name)
    # create_user(settings.user3_email, settings.user3_password, settings.user3_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="IQOL CROWDREPLY", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
