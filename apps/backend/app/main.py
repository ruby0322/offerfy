from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import ensure_schema
from app.routers import auth, chat, health, resumes


@asynccontextmanager
async def lifespan(_application: FastAPI):
    ensure_schema()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Offerfy", lifespan=lifespan)
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(resumes.router)
    application.include_router(chat.router)
    return application


app = create_app()
