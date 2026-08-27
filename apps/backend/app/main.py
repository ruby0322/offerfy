from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import ensure_schema
from app.routers import auth, chat, health, resumes, templates
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
    application.include_router(resumes.router)
    application.include_router(chat.router)
    application.include_router(templates.router)
    return application


app = create_app()
