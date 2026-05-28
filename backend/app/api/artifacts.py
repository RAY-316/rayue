import mimetypes
from urllib.parse import quote

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import Response

from app.api.state import runtime
from app.domain.schemas import ArtifactOut


router = APIRouter()


@router.get("/api/conversations/{conversation_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(conversation_id: str) -> list[ArtifactOut]:
    try:
        return await runtime.list_artifacts(conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/conversations/{conversation_id}/artifacts/download")
async def download_artifact(conversation_id: str, path: str = Query(min_length=1)) -> Response:
    try:
        data, filename = await runtime.read_artifact(conversation_id, path)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    quoted = quote(filename)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
