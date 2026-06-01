import asyncio

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.api.state import runtime
from app.api.state import settings
from app.core.database import get_session
from app.domain.models import Conversation
from app.domain.models import User
from app.domain.models import Workspace
from app.domain.models import utcnow
from app.domain.schemas import UploadedFileOut
from app.files.uploads import UploadValidationError
from app.files.uploads import delete_uploaded_file
from app.files.uploads import list_uploaded_files
from app.files.uploads import resolve_uploaded_file_path
from app.files.uploads import save_uploaded_file
from app.files.object_storage import build_object_storage
from app.files.object_storage import content_type_for_path
from app.files.object_storage import workspace_object_key
from app.files.workspaces import workspace_usage_bytes
from app.files.workspace_index import sync_workspace_file_index


router = APIRouter()


@router.get("/api/conversations/{conversation_id}/uploads", response_model=list[UploadedFileOut])
async def list_uploads(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[UploadedFileOut]:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list_uploaded_files(settings, conversation.workspace_id or conversation_id)


@router.post("/api/conversations/{conversation_id}/uploads", response_model=list[UploadedFileOut])
async def upload_files(
    conversation_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[UploadedFileOut]:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if len(files) > settings.max_upload_files_per_request:
        raise HTTPException(status_code=413, detail="Upload at most 10 files at a time")

    storage_id = conversation.workspace_id or conversation_id
    limit_bytes = settings.workspace_limit_bytes
    used_bytes = workspace_usage_bytes(settings, storage_id)
    saved: list[UploadedFileOut] = []
    try:
        for upload in files:
            saved_file = await save_uploaded_file(
                settings,
                storage_id,
                upload,
                existing_bytes=used_bytes,
                max_total_bytes=limit_bytes,
            )
            saved.append(saved_file)
            used_bytes += saved_file.size
    except UploadValidationError as exc:
        for uploaded in saved:
            delete_uploaded_file(settings, storage_id, uploaded.relative_path)
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    object_storage = build_object_storage(settings)
    if object_storage is not None:
        for uploaded in saved:
            local_path = resolve_uploaded_file_path(settings, storage_id, uploaded.relative_path)
            await asyncio.to_thread(
                object_storage.ensure_path_uploaded,
                key=workspace_object_key(settings, storage_id, uploaded.relative_path),
                path=local_path,
                content_type=content_type_for_path(local_path),
            )

    conversation.last_activity_at = utcnow()
    conversation.updated_at = utcnow()
    if conversation.workspace_id:
        workspace = await session.get(Workspace, conversation.workspace_id)
        if workspace:
            workspace.updated_at = utcnow()
        await sync_workspace_file_index(session, settings, conversation.workspace_id)
    await session.commit()
    await runtime.emit(
        conversation_id,
        "uploads_updated",
        {"count": len(saved), "files": [file.model_dump(mode="json") for file in saved]},
    )
    asyncio.create_task(runtime.sync_uploaded_files(conversation_id))
    return list_uploaded_files(settings, storage_id)
