from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.concurrency import InFlightGate
from app.db import ensure_schema
from app.routers import (
    account,
    applications,
    admin,
    auth,
    chat,
    consent,
    dashboard,
    health,
    jobs,
    matches,
    mcp,
    memory,
    models,
    preferences,
    resumes,
    shares,
    talent,
    talent_me,
    templates,
    usage,
)
from app.services.templates import start_template_prefetch


@asynccontextmanager
async def lifespan(_application: FastAPI):
    ensure_schema()
    start_template_prefetch()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Offerfy", lifespan=lifespan)
    # Outermost middleware, so a request waits for a slot before it touches a
    # router, a dependency or the connection pool. Without it, concurrency
    # beyond the pool's 40 slots collapses the whole worker into QueuePool
    # timeouts -- see app/concurrency.py.
    application.add_middleware(InFlightGate)
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(account.router)
    application.include_router(consent.router)
    application.include_router(resumes.router)
    application.include_router(shares.router)
    application.include_router(jobs.router)
    application.include_router(chat.router)
    application.include_router(memory.router)
    application.include_router(templates.router)
    application.include_router(admin.router)
    application.include_router(talent.router)
    application.include_router(talent_me.router)
    application.include_router(usage.router)
    application.include_router(models.router)
    application.include_router(preferences.router)
    application.include_router(matches.router)
    application.include_router(applications.router)
    application.include_router(dashboard.router)
    application.include_router(mcp.router)
    return application


app = create_app()
