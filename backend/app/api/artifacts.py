import mimetypes
from urllib.parse import quote

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user_from_request
from app.auth import get_current_user
from app.api.state import runtime
from app.core.database import get_session
from app.domain.models import Conversation
from app.domain.models import User
from app.domain.schemas import ArtifactOut


router = APIRouter()


@router.get("/api/conversations/{conversation_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    conversation_id: str,
    sync: bool = Query(True),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ArtifactOut]:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return await runtime.list_artifacts(conversation_id, sync=sync)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/conversations/{conversation_id}/artifacts/download")
async def download_artifact(
    conversation_id: str,
    request: Request,
    path: str = Query(min_length=1),
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    user = await current_user_from_request(request, session=session, token=token)
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
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
