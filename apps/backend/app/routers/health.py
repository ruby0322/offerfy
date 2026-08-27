import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/healthz/sse")
def healthz_sse():
    """Two delayed events so proxies can be checked for SSE buffering."""
    if get_settings().app_env not in {"development", "dev", "test"}:
        raise HTTPException(status_code=404, detail="Not found")

    def generate():
        yield b'data: {"n":1}\n\n'
        time.sleep(0.4)
        yield b'data: {"n":2}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}
