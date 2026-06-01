import asyncio
from urllib.parse import quote

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import Response
from sqlalchemy import delete
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ensure_default_workspace
from app.auth import get_current_user
from app.api.state import runtime
from app.api.state import settings
from app.core.database import get_session
from app.domain.models import ArtifactRecord
from app.domain.models import Conversation
from app.domain.models import User
from app.domain.models import Workspace
from app.domain.models import utcnow
from app.domain.schemas import CreateWorkspaceRequest
from app.domain.schemas import UpdateWorkspaceRequest
from app.domain.schemas import UploadedFileOut
from app.domain.schemas import WorkspaceFileOut
from app.domain.schemas import WorkspaceOut
from app.files.object_storage import build_object_storage
from app.files.object_storage import content_type_for_path
from app.files.object_storage import workspace_object_key
from app.files.uploads import UploadValidationError
from app.files.uploads import delete_uploaded_file
from app.files.uploads import list_uploaded_files
from app.files.uploads import resolve_uploaded_file_path
from app.files.uploads import save_uploaded_file
from app.files.workspaces import WorkspaceFileError
from app.files.workspaces import delete_workspace_file
from app.files.workspaces import read_workspace_file
from app.files.workspaces import workspace_usage_bytes
from app.files.workspace_index import WorkspaceFileIndexError
from app.files.workspace_index import delete_workspace_file_record
from app.files.workspace_index import list_workspace_file_records
from app.files.workspace_index import sync_workspace_file_index


router = APIRouter()


@router.get("/api/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceOut]:
    await ensure_default_workspace(session, settings, user)
    await session.commit()
    rows = (
        await session.execute(
            select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.created_at)
        )
    ).scalars().all()
    return [_workspace_out(row) for row in rows]


@router.post("/api/workspaces", response_model=WorkspaceOut)
async def create_workspace(
    body: CreateWorkspaceRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    count = await session.scalar(select(func.count()).select_from(Workspace).where(Workspace.user_id == user.id))
    if int(count or 0) >= settings.max_workspaces_per_user:
        raise HTTPException(status_code=409, detail="最多只能创建 3 个工作区")
    workspace = Workspace(
        user_id=user.id,
        name=(body.name or f"工作区 {int(count or 0) + 1}").strip()[:120],
        limit_bytes=settings.workspace_limit_bytes,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return _workspace_out(workspace)


@router.patch("/api/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: str,
    body: UpdateWorkspaceRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    workspace = await _owned_workspace(session, user, workspace_id)
    workspace.name = body.name.strip()[:120]
    workspace.updated_at = utcnow()
    await session.commit()
    await session.refresh(workspace)
    return _workspace_out(workspace)


@router.get("/api/workspaces/{workspace_id}/files", response_model=list[WorkspaceFileOut])
async def list_files(
    workspace_id: str,
    kind: str = Query("all"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceFileOut]:
    await _owned_workspace(session, user, workspace_id)
    try:
        await sync_workspace_file_index(session, settings, workspace_id)
        await session.commit()
        return await list_workspace_file_records(session, workspace_id, kind)
    except (WorkspaceFileError, WorkspaceFileIndexError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/workspaces/{workspace_id}/files/input", response_model=list[UploadedFileOut])
async def upload_workspace_files(
    workspace_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[UploadedFileOut]:
    workspace = await _owned_workspace(session, user, workspace_id)
    if len(files) > settings.max_upload_files_per_request:
        raise HTTPException(status_code=413, detail="Upload at most 10 files at a time")

    saved: list[UploadedFileOut] = []
    used_bytes = workspace_usage_bytes(settings, workspace_id)
    try:
        for upload in files:
            saved_file = await save_uploaded_file(
                settings,
                workspace_id,
                upload,
                existing_bytes=used_bytes,
                max_total_bytes=workspace.limit_bytes,
            )
            saved.append(saved_file)
            used_bytes += saved_file.size
    except UploadValidationError as exc:
        for uploaded in saved:
            delete_uploaded_file(settings, workspace_id, uploaded.relative_path)
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    object_storage = build_object_storage(settings)
    if object_storage is not None:
        for uploaded in saved:
            local_path = resolve_uploaded_file_path(settings, workspace_id, uploaded.relative_path)
            await asyncio.to_thread(
                object_storage.ensure_path_uploaded,
                key=workspace_object_key(settings, workspace_id, uploaded.relative_path),
                path=local_path,
                content_type=content_type_for_path(local_path),
            )

    workspace.updated_at = utcnow()
    await sync_workspace_file_index(session, settings, workspace_id)
    await session.commit()
    return list_uploaded_files(settings, workspace_id)


@router.get("/api/workspaces/{workspace_id}/files/download")
async def download_workspace_file(
    workspace_id: str,
    path: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _owned_workspace(session, user, workspace_id)
    try:
        data, filename, media_type = read_workspace_file(settings, workspace_id, path)
    except WorkspaceFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.delete("/api/workspaces/{workspace_id}/files", status_code=204)
async def delete_file(
    workspace_id: str,
    path: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _owned_workspace(session, user, workspace_id)
    active_count = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.status.in_(["queued", "running", "stopping"]),
        )
    )
    if int(active_count or 0) > 0:
        raise HTTPException(status_code=409, detail="任务运行中，暂时不能删除工作区文件")
    try:
        deleted_path = delete_workspace_file(settings, workspace_id, path)
    except WorkspaceFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    object_storage = build_object_storage(settings)
    if object_storage is not None:
        await asyncio.to_thread(
            object_storage.delete_object,
            key=workspace_object_key(settings, workspace_id, deleted_path),
        )
    await session.execute(
        delete(ArtifactRecord).where(
            ArtifactRecord.workspace_id == workspace_id,
            ArtifactRecord.relative_path == deleted_path,
        )
    )
    await delete_workspace_file_record(session, workspace_id, deleted_path)
    await session.commit()
    await runtime.close_workspace_workers(workspace_id)
    return Response(status_code=204)


async def _owned_workspace(session: AsyncSession, user: User, workspace_id: str) -> Workspace:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None or workspace.user_id != user.id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _workspace_out(workspace: Workspace) -> WorkspaceOut:
    used = workspace_usage_bytes(settings, workspace.id)
    return WorkspaceOut(
        id=workspace.id,
        user_id=workspace.user_id,
        name=workspace.name,
        limit_bytes=workspace.limit_bytes,
        used_bytes=used,
        available_bytes=max(0, workspace.limit_bytes - used),
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )
