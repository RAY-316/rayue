from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import WorkspaceFile
from app.domain.models import utcnow
from app.domain.schemas import FileMentionOut
from app.domain.schemas import FileMentionRequest
from app.domain.schemas import WorkspaceFileOut
from app.files.object_storage import content_type_for_path
from app.files.object_storage import workspace_object_key
from app.files.workspaces import list_workspace_files


class WorkspaceFileIndexError(ValueError):
    pass


async def sync_workspace_file_index(
    session: AsyncSession,
    settings: Settings,
    workspace_id: str,
) -> list[WorkspaceFileOut]:
    local_files = list_workspace_files(settings, workspace_id)
    records = (
        await session.execute(
            select(WorkspaceFile).where(WorkspaceFile.workspace_id == workspace_id)
        )
    ).scalars().all()
    records_by_path = {record.relative_path: record for record in records}
    local_paths = {file.relative_path for file in local_files}
    now = utcnow()

    for file in local_files:
        record = records_by_path.get(file.relative_path)
        object_key = workspace_object_key(settings, workspace_id, file.relative_path)
        content_type = content_type_for_path(file.relative_path)
        if record is None:
            session.add(
                WorkspaceFile(
                    workspace_id=workspace_id,
                    kind=file.kind,
                    name=file.name,
                    relative_path=file.relative_path,
                    object_key=object_key,
                    content_type=content_type,
                    version=1,
                    size=file.size,
                    modified_at=file.modified_at,
                    updated_at=now,
                )
            )
            continue

        changed = (
            record.size != file.size
            or _datetime_changed(record.modified_at, file.modified_at)
            or record.deleted_at is not None
        )
        record.kind = file.kind
        record.name = file.name
        record.object_key = object_key
        record.content_type = content_type
        record.size = file.size
        record.modified_at = file.modified_at
        record.deleted_at = None
        record.updated_at = now
        if changed:
            record.version += 1

    for record in records:
        if record.relative_path not in local_paths and record.deleted_at is None:
            record.deleted_at = now
            record.updated_at = now

    await session.flush()
    return await list_workspace_file_records(session, workspace_id)


async def list_workspace_file_records(
    session: AsyncSession,
    workspace_id: str,
    kind: str = "all",
) -> list[WorkspaceFileOut]:
    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"all", "input", "output"}:
        raise WorkspaceFileIndexError("Invalid file kind")
    query = select(WorkspaceFile).where(
        WorkspaceFile.workspace_id == workspace_id,
        WorkspaceFile.deleted_at.is_(None),
    )
    if normalized_kind != "all":
        query = query.where(WorkspaceFile.kind == normalized_kind)
    records = (await session.execute(query)).scalars().all()
    return sorted((_workspace_file_out(record) for record in records), key=lambda item: (item.kind, item.relative_path.lower()))


async def resolve_file_mentions(
    session: AsyncSession,
    settings: Settings,
    workspace_id: str,
    mentions: list[FileMentionRequest],
) -> list[FileMentionOut]:
    if not mentions:
        return []
    if len(mentions) > 20:
        raise WorkspaceFileIndexError("一次最多引用 20 个工作区文件")

    await sync_workspace_file_index(session, settings, workspace_id)
    resolved: list[FileMentionOut] = []
    seen: set[str] = set()
    for mention in mentions:
        record = await _find_mentioned_file(session, workspace_id, mention)
        if record is None:
            raise WorkspaceFileIndexError("引用的工作区文件不存在")
        if record.id in seen:
            continue
        seen.add(record.id)
        resolved.append(
            FileMentionOut(
                file_id=record.id,
                name=record.name,
                relative_path=record.relative_path,
                kind=record.kind,
                type=_file_type(record.relative_path),
                size=record.size,
                version=record.version,
            )
        )
    return resolved


async def delete_workspace_file_record(session: AsyncSession, workspace_id: str, relative_path: str) -> None:
    record = await session.scalar(
        select(WorkspaceFile).where(
            WorkspaceFile.workspace_id == workspace_id,
            WorkspaceFile.relative_path == relative_path,
        )
    )
    if record is not None:
        await session.delete(record)


def render_mentioned_file_context(mentions: list[FileMentionOut]) -> str:
    if not mentions:
        return ""
    lines = [
        "",
        "",
        "User-mentioned workspace files:",
        "Use these exact paths when the user refers to the @ file. Do not guess another file with a similar name.",
    ]
    for file in mentions:
        lines.append(
            f"- @{file.name}: `{file.relative_path}` ({_format_bytes(file.size)}, version {file.version})"
        )
    return "\n".join(lines)


async def _find_mentioned_file(
    session: AsyncSession,
    workspace_id: str,
    mention: FileMentionRequest,
) -> WorkspaceFile | None:
    if mention.file_id:
        return await session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.id == mention.file_id,
                WorkspaceFile.deleted_at.is_(None),
            )
        )
    if mention.relative_path:
        relative_path = _normalize_relative_path(mention.relative_path)
        return await session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.relative_path == relative_path,
                WorkspaceFile.deleted_at.is_(None),
            )
        )
    return None


def _workspace_file_out(record: WorkspaceFile) -> WorkspaceFileOut:
    return WorkspaceFileOut(
        id=record.id,
        name=record.name,
        path=record.relative_path,
        relative_path=record.relative_path,
        kind=record.kind,
        type=_file_type(record.relative_path),
        size=record.size,
        version=record.version,
        modified_at=record.modified_at,
    )


def _normalize_relative_path(path: str) -> str:
    relative = PurePosixPath(path.strip())
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkspaceFileIndexError("Invalid file path")
    return relative.as_posix()


def _datetime_changed(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left != right
    return int(left.timestamp()) != int(right.timestamp())


def _file_type(relative_path: str) -> str:
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}:
        return "image"
    if suffix in {".mp4", ".mov", ".webm"}:
        return "video"
    if suffix in {".mp3", ".wav"}:
        return "audio"
    if suffix in {".pptx", ".pdf", ".docx", ".xlsx", ".csv", ".md", ".txt"}:
        return "document"
    return "file"


def _format_bytes(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    if byte_count < 1024 * 1024 * 1024:
        return f"{byte_count / 1024 / 1024:.1f} MB"
    return f"{byte_count / 1024 / 1024 / 1024:.1f} GB"
