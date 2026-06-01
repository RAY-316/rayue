from __future__ import annotations

import mimetypes
import os
from datetime import datetime
from datetime import timezone
from pathlib import Path
from pathlib import PurePosixPath

from app.core.config import Settings
from app.domain.schemas import WorkspaceFileOut


OUTPUT_DIR = "outputs"
INPUT_DIR = "inputs"


class WorkspaceFileError(ValueError):
    pass


def storage_key(workspace_id: str | None, fallback: str) -> str:
    return workspace_id or fallback


def input_root(settings: Settings, workspace_storage_id: str) -> Path:
    return settings.upload_storage_dir / workspace_storage_id


def artifact_root(settings: Settings, workspace_storage_id: str) -> Path:
    return settings.artifact_storage_dir / workspace_storage_id


def output_root(settings: Settings, workspace_storage_id: str) -> Path:
    return artifact_root(settings, workspace_storage_id) / OUTPUT_DIR


def workspace_usage_bytes(settings: Settings, workspace_storage_id: str, *, exclude: Path | None = None) -> int:
    excluded = exclude.resolve() if exclude is not None else None
    total = _tree_size(input_root(settings, workspace_storage_id), excluded=excluded)
    total += _tree_size(output_root(settings, workspace_storage_id), excluded=excluded)
    return total


def workspace_available_bytes(settings: Settings, workspace_storage_id: str, limit_bytes: int) -> int:
    return max(0, limit_bytes - workspace_usage_bytes(settings, workspace_storage_id))


def list_workspace_files(settings: Settings, workspace_storage_id: str, kind: str = "all") -> list[WorkspaceFileOut]:
    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"all", "input", "output"}:
        raise WorkspaceFileError("Invalid file kind")
    files: list[WorkspaceFileOut] = []
    if normalized_kind in {"all", "input"}:
        files.extend(_list_files(input_root(settings, workspace_storage_id), INPUT_DIR, "input"))
    if normalized_kind in {"all", "output"}:
        files.extend(_list_files(output_root(settings, workspace_storage_id), OUTPUT_DIR, "output"))
    return sorted(files, key=lambda item: (item.kind, item.relative_path.lower()))


def resolve_workspace_file(settings: Settings, workspace_storage_id: str, path: str) -> tuple[Path, str]:
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
        raise WorkspaceFileError("Invalid file path")
    section = relative.parts[0]
    if section == INPUT_DIR:
        root = input_root(settings, workspace_storage_id).resolve()
        inner = PurePosixPath(*relative.parts[1:])
    elif section == OUTPUT_DIR:
        root = output_root(settings, workspace_storage_id).resolve()
        inner = PurePosixPath(*relative.parts[1:])
    else:
        raise WorkspaceFileError("Invalid file path")
    candidate = (root / Path(*inner.parts)).resolve()
    if root != candidate and root not in candidate.parents:
        raise WorkspaceFileError("Invalid file path")
    return candidate, section


def read_workspace_file(settings: Settings, workspace_storage_id: str, path: str) -> tuple[bytes, str, str]:
    local_path, _section = resolve_workspace_file(settings, workspace_storage_id, path)
    if not local_path.is_file():
        raise WorkspaceFileError("File not found")
    media_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    return local_path.read_bytes(), local_path.name, media_type


def delete_workspace_file(settings: Settings, workspace_storage_id: str, path: str) -> str:
    local_path, section = resolve_workspace_file(settings, workspace_storage_id, path)
    if not local_path.is_file():
        raise WorkspaceFileError("File not found")
    local_path.unlink()
    _prune_empty_parents(local_path.parent, input_root(settings, workspace_storage_id) if section == INPUT_DIR else output_root(settings, workspace_storage_id))
    return path


def _list_files(root: Path, prefix: str, kind: str) -> list[WorkspaceFileOut]:
    if not root.exists():
        return []
    files: list[WorkspaceFileOut] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [dirname for dirname in dirnames if not dirname.startswith(".")]
        for filename in filenames:
            if filename.startswith("."):
                continue
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = (PurePosixPath(prefix) / path.relative_to(root).as_posix()).as_posix()
            files.append(
                WorkspaceFileOut(
                    name=path.name,
                    path=relative,
                    relative_path=relative,
                    kind=kind,
                    type=_file_type(relative),
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                )
            )
    return files


def _tree_size(root: Path, *, excluded: Path | None) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        resolved = path.resolve()
        if excluded is not None and resolved == excluded:
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _prune_empty_parents(path: Path, stop: Path) -> None:
    stop = stop.resolve()
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


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
