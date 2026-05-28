import hashlib
import io
import tarfile
from datetime import datetime
from datetime import timezone
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.domain.schemas import SkillOut


@dataclass(frozen=True)
class SkillFile:
    skill_name: str
    relative_path: str
    path: Path
    bytes: int
    modified_ns: int


@dataclass(frozen=True)
class SkillBundle:
    path: Path
    sha256: str
    skill_count: int
    file_count: int
    byte_count: int
    skill_names: tuple[str, ...]


def list_admin_skills(settings: Settings) -> list[SkillOut]:
    root = settings.admin_skills_dir
    root.mkdir(parents=True, exist_ok=True)
    skills: list[SkillOut] = []
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        files = [path for path in skill_dir.rglob("*") if path.is_file()]
        stats = [path.stat() for path in files]
        total_bytes = sum(stat.st_size for stat in stats)
        updated_at = max((stat.st_mtime for stat in stats), default=skill_file.stat().st_mtime)
        skills.append(
            SkillOut(
                name=skill_dir.name,
                path=str(skill_file),
                bytes=total_bytes,
                updated_at=datetime.fromtimestamp(updated_at, timezone.utc),
            )
        )
    return skills


def read_skill_tree(settings: Settings) -> dict[str, list[SkillFile]]:
    root = settings.admin_skills_dir
    root.mkdir(parents=True, exist_ok=True)
    skills: dict[str, list[SkillFile]] = {}
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if not (skill_dir / "SKILL.md").exists():
            continue
        files: list[SkillFile] = []
        for path in sorted(item for item in skill_dir.rglob("*") if item.is_file()):
            stat = path.stat()
            files.append(
                SkillFile(
                    skill_name=skill_dir.name,
                    relative_path=path.relative_to(skill_dir).as_posix(),
                    path=path,
                    bytes=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )
        skills[skill_dir.name] = files
    return skills


def build_skill_bundle(settings: Settings) -> SkillBundle:
    skills = read_skill_tree(settings)
    settings.skill_bundle_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    files = [file for skill_files in skills.values() for file in skill_files]
    file_payloads: list[tuple[SkillFile, bytes]] = []
    byte_count = 0
    for file in files:
        data = file.path.read_bytes()
        archive_name = f"{file.skill_name}/{file.relative_path}"
        digest.update(archive_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
        digest.update(b"\0")
        byte_count += len(data)
        file_payloads.append((file, data))

    sha256 = digest.hexdigest()
    archive_path = settings.skill_bundle_dir / f"admin-skills-{sha256[:16]}.tar.gz"
    if not archive_path.exists():
        temporary_path = settings.skill_bundle_dir / "admin-skills.tmp.tar.gz"
        with tarfile.open(temporary_path, "w:gz") as tar:
            for file, data in file_payloads:
                info = tarfile.TarInfo(f"{file.skill_name}/{file.relative_path}")
                info.size = len(data)
                info.mtime = file.modified_ns // 1_000_000_000
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
        temporary_path.replace(archive_path)

    for old_path in settings.skill_bundle_dir.glob("admin-skills-*.tar.gz"):
        if old_path != archive_path:
            old_path.unlink(missing_ok=True)

    return SkillBundle(
        path=archive_path,
        sha256=sha256,
        skill_count=len(skills),
        file_count=len(files),
        byte_count=byte_count,
        skill_names=tuple(sorted(skills)),
    )


def upsert_admin_skill(settings: Settings, name: str, content: str) -> SkillOut:
    skill_dir = settings.admin_skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    stat = skill_file.stat()
    return SkillOut(
        name=name,
        path=str(skill_file),
        bytes=stat.st_size,
        updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
    )
