import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.concurrency import SCOPE_KEY, gate_state
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
def readyz(request: Request, db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    # Saturation, reported rather than inferred. On 2026-11-20 a worker served
    # ~10k 500s an hour while this endpoint answered a bare "ok", and working
    # out that it was at capacity took `ss`, `pg_stat_activity` and a load
    # test. `gate` makes it one curl: `in_flight` at `limit` with `waiting`
    # above zero is a saturated worker, and `shed` counts what it turned away.
    # Ungated itself, so it still answers when every slot is taken.
    # Read off this request's own scope: the gate that is actually above this
    # handler, not the last one built in the process.
    return {"status": "ok", "gate": gate_state(request.scope.get(SCOPE_KEY))}
