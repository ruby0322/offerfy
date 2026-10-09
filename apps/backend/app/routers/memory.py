from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, load_resume_for_owner, owner_context, require_user
from app.models import MemoryItem, User
from app.schemas import MemoryItemOut
from app.services.family import family_master
from app.services.memory import (
    MemoryFullError,
    accept_memory,
    dismiss_memory,
    list_live_memory,
)

router = APIRouter()


def _out(item: MemoryItem) -> MemoryItemOut:
    return MemoryItemOut(
        id=item.id,
        text=item.text,
        status=item.status,
        source_resume_id=item.source_resume_id,
        created_at=item.created_at.isoformat() if item.created_at else "",
    )


def _item_for_owner(
    item_id: str,
    request: Request,
    response: Response,
    db: Session,
    user: User | None,
    *,
    user_only: bool,
) -> MemoryItem:
    item = db.get(MemoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if user_only:
        load_resume_for_owner(item.master_resume_id, request, db, user, None)
    else:
        owner, guest = owner_context(request, response, db, user, ensure=False)
        load_resume_for_owner(item.master_resume_id, request, db, owner, guest)
    return item


@router.get("/v1/resumes/{resume_id}/memory", response_model=list[MemoryItemOut])
def list_memory(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    owner, guest = owner_context(request, response, db, user, ensure=False)
    member = load_resume_for_owner(resume_id, request, db, owner, guest)
    master = family_master(db, member)
    return [_out(item) for item in list_live_memory(db, master.id)]


@router.post("/v1/memory/{item_id}/accept", response_model=MemoryItemOut)
def accept(
    item_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    item = _item_for_owner(item_id, request, response, db, user, user_only=True)
    try:
        accept_memory(db, item)
    except MemoryFullError as exc:
        raise HTTPException(status_code=409, detail="Memory is full") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(item)


@router.post("/v1/memory/{item_id}/dismiss", response_model=MemoryItemOut)
def dismiss(
    item_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    item = _item_for_owner(item_id, request, response, db, user, user_only=False)
    dismiss_memory(db, item)
    return _out(item)


@router.delete("/v1/memory/{item_id}", status_code=204)
def delete(
    item_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    item = _item_for_owner(item_id, request, response, db, user, user_only=True)
    if item.status != "accepted":
        raise HTTPException(status_code=400, detail="Only accepted memory can be deleted")
    db.delete(item)
    return RawResponse(status_code=204)
