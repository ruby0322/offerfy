from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.templates import find_thumbnail, list_templates, thumbnail_media_type

router = APIRouter()


@router.get("/v1/templates")
def templates():
    return {"templates": list_templates()}


@router.get("/v1/templates/{name}/preview")
def template_preview(name: str):
    path = find_thumbnail(name)
    if path is None:
        raise HTTPException(status_code=404, detail="preview not found")
    return FileResponse(path, media_type=thumbnail_media_type(path))
