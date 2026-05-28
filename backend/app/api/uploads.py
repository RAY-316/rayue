import asyncio

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import runtime
from app.api.state import settings
from app.core.database import get_session
from app.domain.models import Conversation
from app.domain.models import utcnow
from app.domain.schemas import UploadedFileOut
from app.files.uploads import UploadValidationError
from app.files.uploads import delete_uploaded_file
from app.files.uploads import list_uploaded_files
from app.files.uploads import save_uploaded_file


router = APIRouter()


@router.get("/api/conversations/{conversation_id}/uploads", response_model=list[UploadedFileOut])
async def list_uploads(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[UploadedFileOut]:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list_uploaded_files(settings, conversation_id)


@router.post("/api/conversations/{conversation_id}/uploads", response_model=list[UploadedFileOut])
async def upload_files(
    conversation_id: str,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
) -> list[UploadedFileOut]:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if len(files) > settings.max_upload_files_per_request:
        raise HTTPException(status_code=413, detail="Upload at most 10 files at a time")

    saved: list[UploadedFileOut] = []
    try:
        for upload in files:
            saved.append(await save_uploaded_file(settings, conversation_id, upload))
    except UploadValidationError as exc:
        for uploaded in saved:
            delete_uploaded_file(settings, conversation_id, uploaded.relative_path)
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    conversation.last_activity_at = utcnow()
    conversation.updated_at = utcnow()
    await session.commit()
    await runtime.emit(
        conversation_id,
        "uploads_updated",
        {"count": len(saved), "files": [file.model_dump(mode="json") for file in saved]},
    )
    asyncio.create_task(runtime.sync_uploaded_files(conversation_id))
    return list_uploaded_files(settings, conversation_id)
