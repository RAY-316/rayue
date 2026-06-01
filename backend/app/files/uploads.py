import hashlib
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from pathlib import PurePosixPath

from fastapi import UploadFile

from app.core.config import Settings
from app.domain.schemas import UploadedFileOut


CHUNK_SIZE = 1024 * 1024
INPUT_MARKER_NAME = ".rayue-agent-inputs.sha256"


@dataclass(frozen=True)
class UploadBundle:
    path: Path
    sha256: str
    file_count: int
    byte_count: int
    files: tuple[UploadedFileOut, ...]


class UploadValidationError(ValueError):
    pass


async def save_uploaded_file(
    settings: Settings,
    conversation_id: str,
    upload: UploadFile,
    *,
    existing_bytes: int = 0,
    max_total_bytes: int | None = None,
) -> UploadedFileOut:
    root = _upload_root(settings, conversation_id)
    root.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(root, _sanitize_filename(upload.filename))
    written = 0
    try:
        with destination.open("wb") as file:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.max_upload_file_bytes:
                    raise UploadValidationError(
                        f"{upload.filename or 'upload'} exceeds the "
                        f"{_format_upload_limit(settings.max_upload_file_bytes)} per-file limit"
                    )
                if max_total_bytes is not None and existing_bytes + written > max_total_bytes:
                    raise UploadValidationError(
                        f"{upload.filename or 'upload'} exceeds the workspace storage limit"
                    )
                file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return _uploaded_file_out(settings, _upload_root(settings, conversation_id), destination)


def list_uploaded_files(settings: Settings, conversation_id: str) -> list[UploadedFileOut]:
    root = _upload_root(settings, conversation_id)
    if not root.exists():
        return []
    return [
        _uploaded_file_out(settings, root, path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]


def _format_upload_limit(byte_count: int) -> str:
    megabytes = byte_count / (1024 * 1024)
    if megabytes.is_integer():
        return f"{int(megabytes)} MB"
    return f"{megabytes:.1f} MB"


def delete_uploaded_file(settings: Settings, conversation_id: str, path: str) -> None:
    local_path = _resolve_uploaded_relative_path(settings, conversation_id, path)
    local_path.unlink(missing_ok=True)


def resolve_uploaded_file_path(settings: Settings, conversation_id: str, path: str) -> Path:
    return _resolve_uploaded_relative_path(settings, conversation_id, path)


def render_upload_context(settings: Settings, conversation_id: str) -> str:
    files = list_uploaded_files(settings, conversation_id)
    if not files:
        return ""
    lines = [
        "",
        "",
        "Uploaded input files are available in the current workspace.",
        f"Treat `{settings.sandbox_inputs_dir}/` as read-only input/reference material.",
        "Write intermediate work under `work/` or `tmp/`.",
        "Copy only final user-facing deliverables into `outputs/`; only `outputs/` is saved and shown to the user.",
        "Input file paths:",
    ]
    for file in files:
        lines.append(f"- `{file.relative_path}` ({_format_bytes(file.size)})")
    return "\n".join(lines)


def build_upload_bundle(settings: Settings, conversation_id: str) -> UploadBundle:
    files = list_uploaded_files(settings, conversation_id)
    settings.upload_bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_root = settings.upload_bundle_dir / conversation_id
    bundle_root.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    byte_count = 0
    local_root = _upload_root(settings, conversation_id)
    for file in files:
        local_path = _resolve_uploaded_relative_path(settings, conversation_id, file.relative_path)
        digest.update(file.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_sha256(local_path).digest())
        digest.update(b"\0")
        byte_count += file.size

    sha256 = digest.hexdigest()
    archive_path = bundle_root / f"inputs-{sha256[:16]}.tar"
    if files and not archive_path.exists():
        temporary_path = bundle_root / "inputs.tmp.tar"
        with tarfile.open(temporary_path, "w") as tar:
            for file in files:
                local_path = _resolve_uploaded_relative_path(
                    settings,
                    conversation_id,
                    file.relative_path,
                )
                tar.add(local_path, arcname=local_path.relative_to(local_root).as_posix())
        temporary_path.replace(archive_path)

    for old_path in bundle_root.glob("inputs-*.tar"):
        if old_path != archive_path:
            old_path.unlink(missing_ok=True)

    return UploadBundle(
        path=archive_path,
        sha256=sha256,
        file_count=len(files),
        byte_count=byte_count,
        files=tuple(files),
    )


def uploaded_relative_path(settings: Settings, path: str) -> str:
    relative = PurePosixPath(path)
    input_dir = PurePosixPath(settings.sandbox_inputs_dir)
    if relative.is_absolute() or ".." in relative.parts:
        raise UploadValidationError("Invalid upload path")
    if relative.parts and relative.parts[0] == input_dir.name:
        return relative.as_posix()
    return (input_dir / relative).as_posix()


def is_uploaded_input_path(settings: Settings, relative_path: str) -> bool:
    relative = PurePosixPath(relative_path)
    return bool(relative.parts) and relative.parts[0] == PurePosixPath(settings.sandbox_inputs_dir).name


def _upload_root(settings: Settings, conversation_id: str) -> Path:
    return settings.upload_storage_dir / conversation_id


def _uploaded_file_out(settings: Settings, root: Path, path: Path) -> UploadedFileOut:
    stat = path.stat()
    relative_name = path.relative_to(root).as_posix()
    sandbox_path = (PurePosixPath(settings.sandbox_inputs_dir) / PurePosixPath(relative_name)).as_posix()
    return UploadedFileOut(
        name=path.name,
        path=sandbox_path,
        relative_path=sandbox_path,
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
    )


def _resolve_uploaded_relative_path(settings: Settings, conversation_id: str, path: str) -> Path:
    root = _upload_root(settings, conversation_id).resolve()
    relative = PurePosixPath(uploaded_relative_path(settings, path))
    input_dir = PurePosixPath(settings.sandbox_inputs_dir)
    if relative.parts and relative.parts[0] == input_dir.name:
        relative = PurePosixPath(*relative.parts[1:])
    candidate = (root / Path(*relative.parts)).resolve()
    if root != candidate and root not in candidate.parents:
        raise UploadValidationError("Invalid upload path")
    return candidate


def _sanitize_filename(filename: str | None) -> str:
    name = (filename or "upload").replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    if not name or name in {".", "..", INPUT_MARKER_NAME}:
        name = "upload"
    if len(name) > 180:
        path = Path(name)
        suffix = path.suffix[:24]
        stem = path.stem[: 180 - len(suffix)]
        name = f"{stem}{suffix}" or "upload"
    return name


def _unique_destination(root: Path, filename: str) -> Path:
    candidate = root / filename
    if not candidate.exists():
        return candidate
    path = Path(filename)
    stem = path.stem or "upload"
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = root / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise UploadValidationError("Could not allocate a unique upload filename")


def _file_sha256(path: Path) -> "hashlib._Hash":
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest


def _format_bytes(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count / 1024 / 1024:.1f} MB"
