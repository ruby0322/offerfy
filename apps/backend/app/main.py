from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import ensure_schema
from app.routers import (
    account,
    admin,
    auth,
    chat,
    consent,
    health,
    jobs,
    memory,
    models,
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
    return application


app = create_app()
