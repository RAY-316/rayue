import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import shlex
import tarfile
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import desc
from sqlalchemy import select

from app.admin.skills import build_skill_bundle
from app.agent.codex_config import render_codex_config
from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.event_bus import event_bus
from app.domain.models import AgentEvent
from app.domain.models import AgentTurn
from app.domain.models import ArtifactRecord
from app.domain.models import Conversation
from app.domain.models import Message
from app.domain.models import utcnow
from app.domain.schemas import ArtifactOut
from app.files.uploads import INPUT_MARKER_NAME
from app.files.uploads import build_upload_bundle
from app.files.uploads import is_uploaded_input_path
from app.files.uploads import render_upload_context


class AgentRuntimeError(RuntimeError):
    pass


class CodexRpcError(AgentRuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message") or json.dumps(error, ensure_ascii=False))
        self.error = error


class AgentTurnInterrupted(AgentRuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactBundle:
    path: Path
    sha256: str
    file_count: int
    byte_count: int
    files: tuple[ArtifactOut, ...]


ARTIFACT_EXTENSIONS = (
    ".pptx",
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".webp",
    ".mp4",
    ".mov",
    ".webm",
    ".mp3",
    ".wav",
    ".zip",
    ".html",
)
ARTIFACT_PRIORITY_EXTENSIONS = (
    ".pptx",
    ".pdf",
    ".docx",
    ".xlsx",
    ".zip",
    ".html",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".webp",
)
ARTIFACT_EXCLUDED_DIRS = frozenset(
    {
        ".cache",
        ".git",
        ".mypy_cache",
        ".next",
        ".npm",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "env",
        "node_modules",
        "venv",
    }
)
ARTIFACT_EXCLUDED_FILES = frozenset(
    {
        ".package-lock.json",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
)
ARTIFACT_PERSIST_RETRY_DELAYS_SECONDS = (2, 5, 10, 13)
ARTIFACT_MARKER_NAME = ".rayue-artifacts.sha256"
BACKFILL_TIMEOUT_SECONDS = 300
REMOTE_COMMAND_TIMEOUT_SECONDS = 300
REMOTE_LONG_OPERATION_TIMEOUT_SECONDS = 600
SHORT_REMOTE_COMMAND_TIMEOUT_SECONDS = 120
TURN_COMPLETION_TIMEOUT_SECONDS = 7200
TURN_PROGRESS_HEARTBEAT_SECONDS = 30
POST_COMMAND_SILENCE_RECOVERY_SECONDS = 300
POST_AGENT_MESSAGE_SILENCE_RECOVERY_SECONDS = 180
MODEL_SILENCE_RECOVERY_SECONDS = 900
TURN_RECOVERY_READ_TIMEOUT_SECONDS = 30
TURN_INTERRUPT_TIMEOUT_SECONDS = 30


def _event_payload(event: AgentEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "conversation_id": event.conversation_id,
        "type": event.type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


class AgentRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._workers: dict[str, BaseConversationWorker] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._turn_tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_requests: set[str] = set()
        self._sweeper_task: asyncio.Task | None = None
        self._artifact_event_signatures: dict[str, tuple[tuple[str, int], ...]] = {}
        self._active_turn_ids: dict[str, str] = {}
        self._artifact_record_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        build_skill_bundle(self.settings)
        await self._recover_interrupted_conversations()
        if self._sweeper_task is None:
            self._sweeper_task = asyncio.create_task(self._sweep_idle_workers())

    async def shutdown(self) -> None:
        if self._sweeper_task:
            self._sweeper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper_task
        await asyncio.gather(*(worker.close() for worker in list(self._workers.values())), return_exceptions=True)
        self._workers.clear()

    async def delete_conversation(self, conversation_id: str) -> None:
        task = self._turn_tasks.pop(conversation_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        worker = self._workers.pop(conversation_id, None)
        if worker is not None:
            await worker.close()
        self._locks.pop(conversation_id, None)
        self._artifact_record_locks.pop(conversation_id, None)
        self._stop_requests.discard(conversation_id)
        self._artifact_event_signatures.pop(conversation_id, None)
        shutil.rmtree(self.settings.artifact_storage_dir / conversation_id, ignore_errors=True)
        shutil.rmtree(self.settings.upload_storage_dir / conversation_id, ignore_errors=True)
        shutil.rmtree(self.settings.upload_bundle_dir / conversation_id, ignore_errors=True)

    def schedule_turn(self, conversation_id: str, content: str, turn_id: str) -> None:
        self._locks.setdefault(conversation_id, asyncio.Lock())
        task = asyncio.create_task(self.run_turn(conversation_id, content, turn_id))
        self._turn_tasks[conversation_id] = task

        def clear_task(completed_task: asyncio.Task[None]) -> None:
            if self._turn_tasks.get(conversation_id) is completed_task:
                self._turn_tasks.pop(conversation_id, None)

        task.add_done_callback(clear_task)

    async def run_turn(self, conversation_id: str, content: str, turn_id: str) -> None:
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            self._active_turn_ids[conversation_id] = turn_id
            if conversation_id in self._stop_requests:
                self._stop_requests.discard(conversation_id)
                await self._update_turn_state(turn_id, status="interrupted", phase="interrupted", completed=True)
                await self.emit(conversation_id, "turn_interrupted", {"artifacts": []})
                await self._set_conversation_status(conversation_id, "idle")
                self._active_turn_ids.pop(conversation_id, None)
                return

            worker = self._workers.get(conversation_id)
            try:
                await self._update_turn_state(turn_id, status="running", phase="startup")
                await self._set_conversation_status(conversation_id, "running")
                if worker is None or worker.closed:
                    worker = self._new_worker(conversation_id)
                    self._workers[conversation_id] = worker
                if conversation_id in self._stop_requests:
                    raise AgentTurnInterrupted()
                context = render_upload_context(self.settings, conversation_id)
                context += await self._render_conversation_context(conversation_id, content)
                context += self._render_artifact_context(conversation_id)
                await worker.send_message(content + context)
                artifacts = await self._persist_and_emit_artifacts(conversation_id, worker, "turn_success")
                if artifacts and not await self._has_assistant_after_current_user(
                    conversation_id,
                    content,
                ):
                    await self._persist_completion_message(conversation_id, artifacts)
                await self._update_turn_state(
                    turn_id,
                    status="completed",
                    phase="completed",
                    artifact_count=len(artifacts),
                    completed=True,
                )
                await self._set_conversation_status(conversation_id, "idle")
            except AgentTurnInterrupted:
                self._stop_requests.discard(conversation_id)
                artifacts = (
                    await self._persist_and_emit_artifacts(
                        conversation_id,
                        worker,
                        "turn_interrupted",
                    )
                    if worker is not None
                    else self._list_local_artifacts(conversation_id)
                )
                await self.emit(
                    conversation_id,
                    "turn_interrupted",
                    {"artifacts": [artifact.model_dump(mode="json") for artifact in artifacts[:20]]},
                )
                await self._update_turn_state(
                    turn_id,
                    status="interrupted",
                    phase="interrupted",
                    artifact_count=len(artifacts),
                    completed=True,
                )
                with contextlib.suppress(Exception):
                    await worker.close()
                self._workers.pop(conversation_id, None)
                await self._set_conversation_status(conversation_id, "idle")
            except Exception as exc:
                artifacts = (
                    await self._persist_and_emit_artifacts(
                        conversation_id,
                        worker,
                        "turn_error",
                    )
                    if worker is not None
                    else self._list_local_artifacts(conversation_id)
                )
                safe_message = _safe_user_error_message(exc)
                if artifacts:
                    await self._persist_recovery_message(conversation_id, artifacts)
                    await self.emit(
                        conversation_id,
                        "turn_recovered",
                        {
                            "message": "已保存生成文件，但最终回复没有正常结束。",
                            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts[:20]],
                        },
                    )
                    await self._update_turn_state(
                        turn_id,
                        status="recovered",
                        phase="recovered",
                        error=safe_message,
                        artifact_count=len(artifacts),
                        completed=True,
                    )
                    await self._set_conversation_status(conversation_id, "idle")
                else:
                    await self._update_turn_state(
                        turn_id,
                        status="failed",
                        phase="failed",
                        error=safe_message,
                        artifact_count=0,
                        completed=True,
                    )
                    await self._set_conversation_status(conversation_id, "error", safe_message)
                    await self.emit(conversation_id, "error", {"message": safe_message})
            finally:
                if self._active_turn_ids.get(conversation_id) == turn_id:
                    self._active_turn_ids.pop(conversation_id, None)

    async def sync_uploaded_files(self, conversation_id: str) -> None:
        worker = self._workers.get(conversation_id)
        if worker is not None and not worker.closed:
            await worker.sync_uploaded_files()

    async def stop_turn(self, conversation_id: str) -> None:
        self._stop_requests.add(conversation_id)
        worker = self._workers.get(conversation_id)
        task = self._turn_tasks.get(conversation_id)
        if (worker is None or worker.closed) and (task is None or task.done()):
            await self._set_conversation_status(conversation_id, "stopping")
            self._stop_requests.discard(conversation_id)
            await self._set_conversation_status(conversation_id, "idle")
            return

        await self._set_conversation_status(conversation_id, "stopping")
        asyncio.create_task(self._stop_turn_background(conversation_id))

    async def _stop_turn_background(self, conversation_id: str) -> None:
        worker = self._workers.get(conversation_id)
        try:
            if worker is not None and not worker.closed:
                await worker.interrupt_current_turn()
        except AgentTurnInterrupted:
            pass
        except Exception:
            if worker is not None:
                with contextlib.suppress(Exception):
                    await worker.close()
                self._workers.pop(conversation_id, None)
        finally:
            await self._wait_for_turn_task(conversation_id)
            if conversation_id in self._stop_requests:
                self._stop_requests.discard(conversation_id)
                await self._set_conversation_status(conversation_id, "idle")

    async def _wait_for_turn_task(self, conversation_id: str) -> None:
        task = self._turn_tasks.get(conversation_id)
        if task is not None and not task.done():
            await asyncio.gather(asyncio.shield(task), return_exceptions=True)
            return
        lock = self._locks.get(conversation_id)
        if lock is not None and lock.locked():
            async with lock:
                pass

    async def emit(self, conversation_id: str, event_type: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        active_turn_id = self._active_turn_ids.get(conversation_id)
        if active_turn_id and "rayueTurnId" not in payload:
            payload["rayueTurnId"] = active_turn_id
        async with SessionLocal() as session:
            conversation = await session.get(Conversation, conversation_id)
            if not conversation:
                return
            now = utcnow()
            if active_turn_id and event_type != "turn_watchdog":
                turn = await session.get(AgentTurn, active_turn_id)
                if turn:
                    turn.last_event_at = now
                    turn.updated_at = now
            event = AgentEvent(conversation_id=conversation_id, type=event_type, payload=payload)
            session.add(event)
            conversation.last_activity_at = now
            conversation.updated_at = now
            await session.commit()
            await session.refresh(event)
            data = _event_payload(event)
        await event_bus.publish(conversation_id, data)

    async def _update_turn_state(
        self,
        turn_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        error: str | None = None,
        codex_turn_id: str | None = None,
        artifact_count: int | None = None,
        command_started: bool = False,
        command_completed: bool = False,
        completed: bool = False,
    ) -> None:
        async with SessionLocal() as session:
            turn = await session.get(AgentTurn, turn_id)
            if not turn:
                return
            now = utcnow()
            if status is not None:
                turn.status = status
            if phase is not None:
                turn.phase = phase
            if error is not None:
                turn.error = error
            if codex_turn_id is not None:
                turn.codex_turn_id = codex_turn_id
            if artifact_count is not None:
                turn.artifact_count = artifact_count
            if command_started:
                turn.last_command_started_at = now
            if command_completed:
                turn.last_command_completed_at = now
            if completed:
                turn.completed_at = now
            turn.last_event_at = now
            turn.updated_at = now
            await session.commit()

    async def _update_active_turn(
        self,
        conversation_id: str,
        **kwargs: Any,
    ) -> None:
        turn_id = self._active_turn_ids.get(conversation_id)
        if not turn_id:
            return
        await self._update_turn_state(turn_id, **kwargs)

    async def persist_assistant_message(self, conversation_id: str, item_id: str, text: str) -> None:
        if not text.strip():
            return
        async with SessionLocal() as session:
            conversation = await session.get(Conversation, conversation_id)
            if not conversation:
                return
            existing = await session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.item_id == item_id,
                    Message.role == "assistant",
                )
            )
            if existing:
                existing.content = text
                existing.created_at = utcnow()
            else:
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=text,
                        item_id=item_id,
                    )
                )
            conversation.last_activity_at = utcnow()
            conversation.updated_at = utcnow()
            await session.commit()

    async def update_codex_thread(
        self,
        conversation_id: str,
        *,
        codex_thread_id: str | None = None,
        sandbox_id: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        async with SessionLocal() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation:
                if codex_thread_id:
                    conversation.codex_thread_id = codex_thread_id
                if sandbox_id:
                    conversation.sandbox_id = sandbox_id
                if workspace_path:
                    conversation.workspace_path = workspace_path
                conversation.updated_at = utcnow()
                await session.commit()

    async def list_artifacts(self, conversation_id: str) -> list[ArtifactOut]:
        local = self._list_local_artifacts(conversation_id)
        worker = self._workers.get(conversation_id)
        if worker is not None and not worker.closed:
            try:
                await worker.persist_artifacts()
                refreshed = self._list_local_artifacts(conversation_id)
                if refreshed:
                    return await self._attach_artifact_records(conversation_id, refreshed)
            except Exception:
                pass
        if local:
            return await self._attach_artifact_records(conversation_id, local)
        try:
            worker = await self._artifact_worker(conversation_id)
            await worker.persist_artifacts()
            refreshed = self._list_local_artifacts(conversation_id)
            if refreshed:
                return await self._attach_artifact_records(conversation_id, refreshed)
            return await self._attach_artifact_records(conversation_id, local)
        except Exception:
            return await self._attach_artifact_records(conversation_id, local)

    async def read_artifact(self, conversation_id: str, path: str) -> tuple[bytes, str]:
        if self._is_uploaded_input_artifact_path(path):
            raise AgentRuntimeError("Uploaded input files are not downloadable artifacts")
        local_path = self._resolve_local_artifact_path(conversation_id, path)
        if local_path.exists() and local_path.is_file():
            return local_path.read_bytes(), local_path.name
        worker = await self._artifact_worker(conversation_id)
        return await worker.read_artifact(path)

    async def _persist_and_emit_artifacts(
        self,
        conversation_id: str,
        worker: "BaseConversationWorker",
        reason: str,
    ) -> list[ArtifactOut]:
        last_error: Exception | None = None
        for delay in (0, *ARTIFACT_PERSIST_RETRY_DELAYS_SECONDS):
            if delay:
                await asyncio.sleep(delay)
            try:
                artifacts = await worker.persist_artifacts()
                if not artifacts:
                    artifacts = self._list_local_artifacts(conversation_id)
                await self.emit_artifacts_updated(conversation_id, artifacts, reason)
                return artifacts
            except Exception as exc:
                last_error = exc

        artifacts = self._list_local_artifacts(conversation_id)
        await self.emit_artifacts_updated(conversation_id, artifacts, reason)
        if last_error is not None:
            await self.emit(
                conversation_id,
                "artifact_persist_failed",
                {"message": _safe_user_error_message(last_error), "reason": reason},
            )
        return artifacts

    async def emit_artifacts_updated(
        self,
        conversation_id: str,
        artifacts: list[ArtifactOut],
        reason: str,
    ) -> None:
        if not artifacts:
            return
        artifacts = await self._upsert_artifact_records(conversation_id, artifacts)
        active_turn_id = self._active_turn_ids.get(conversation_id)
        if active_turn_id:
            await self._update_turn_state(active_turn_id, artifact_count=len(artifacts))
        signature = tuple((artifact.relative_path, artifact.size) for artifact in artifacts)
        previous_signature = self._artifact_event_signatures.get(conversation_id)
        if previous_signature is None:
            previous_signature = await self._load_latest_artifact_signature(conversation_id)
            if previous_signature is not None:
                self._artifact_event_signatures[conversation_id] = previous_signature
        if previous_signature == signature:
            return
        previous_sizes = dict(previous_signature or ())
        changed_artifacts = [
            artifact
            for artifact in artifacts
            if previous_sizes.get(artifact.relative_path) != artifact.size
        ]
        if previous_signature is not None and not changed_artifacts:
            self._artifact_event_signatures[conversation_id] = signature
            return
        unchanged_artifacts = [
            artifact
            for artifact in artifacts
            if previous_sizes.get(artifact.relative_path) == artifact.size
        ]
        visible_artifacts = [*changed_artifacts, *unchanged_artifacts]
        self._artifact_event_signatures[conversation_id] = signature
        await self.emit(
            conversation_id,
            "artifacts_updated",
            {
                "count": len(artifacts),
                "storage": "local",
                "reason": reason,
                "turnId": active_turn_id,
                "artifacts": [artifact.model_dump(mode="json") for artifact in visible_artifacts[:40]],
                "changed_artifacts": [
                    artifact.model_dump(mode="json") for artifact in changed_artifacts[:40]
                ],
            },
        )

    async def _upsert_artifact_records(
        self,
        conversation_id: str,
        artifacts: list[ArtifactOut],
    ) -> list[ArtifactOut]:
        active_turn_id = self._active_turn_ids.get(conversation_id)
        now = utcnow()
        lock = self._artifact_record_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            return await self._upsert_artifact_records_locked(
                conversation_id,
                artifacts,
                active_turn_id,
                now,
            )

    async def _upsert_artifact_records_locked(
        self,
        conversation_id: str,
        artifacts: list[ArtifactOut],
        active_turn_id: str | None,
        now: datetime,
    ) -> list[ArtifactOut]:
        async with SessionLocal() as session:
            for artifact in artifacts:
                existing = await session.scalar(
                    select(ArtifactRecord).where(
                        ArtifactRecord.conversation_id == conversation_id,
                        ArtifactRecord.relative_path == artifact.relative_path,
                    )
                )
                if existing:
                    changed = existing.size != artifact.size or existing.path != artifact.path
                    existing.name = artifact.name
                    existing.path = artifact.path
                    existing.type = artifact.type
                    existing.size = artifact.size
                    existing.modified_at = artifact.modified_at
                    existing.updated_at = now
                    if active_turn_id and (changed or existing.turn_id is None):
                        existing.turn_id = active_turn_id
                    continue
                session.add(
                    ArtifactRecord(
                        conversation_id=conversation_id,
                        turn_id=active_turn_id,
                        name=artifact.name,
                        path=artifact.path,
                        relative_path=artifact.relative_path,
                        type=artifact.type,
                        size=artifact.size,
                        modified_at=artifact.modified_at,
                        first_seen_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()
        return await self._attach_artifact_records(conversation_id, artifacts)

    async def _attach_artifact_records(
        self,
        conversation_id: str,
        artifacts: list[ArtifactOut],
    ) -> list[ArtifactOut]:
        if not artifacts:
            return artifacts
        paths = [artifact.relative_path for artifact in artifacts]
        async with SessionLocal() as session:
            records = (
                await session.execute(
                    select(ArtifactRecord).where(
                        ArtifactRecord.conversation_id == conversation_id,
                        ArtifactRecord.relative_path.in_(paths),
                    )
                )
            ).scalars().all()
        by_path = {record.relative_path: record for record in records}
        attached: list[ArtifactOut] = []
        for artifact in artifacts:
            record = by_path.get(artifact.relative_path)
            if not record:
                attached.append(artifact)
                continue
            attached.append(
                artifact.model_copy(
                    update={
                        "turn_id": record.turn_id,
                        "first_seen_at": record.first_seen_at,
                    }
                )
            )
        return attached

    async def _load_latest_artifact_signature(
        self,
        conversation_id: str,
    ) -> tuple[tuple[str, int], ...] | None:
        async with SessionLocal() as session:
            event = await session.scalar(
                select(AgentEvent)
                .where(
                    AgentEvent.conversation_id == conversation_id,
                    AgentEvent.type == "artifacts_updated",
                )
                .order_by(desc(AgentEvent.created_at))
                .limit(1)
            )
        if event is None:
            return None
        artifacts = event.payload.get("artifacts")
        if not isinstance(artifacts, list):
            return None
        signature: list[tuple[str, int]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            relative_path = artifact.get("relative_path") or artifact.get("relativePath")
            size = artifact.get("size")
            if isinstance(relative_path, str) and isinstance(size, int):
                signature.append((relative_path, size))
        return tuple(signature) or None

    def _list_local_artifacts(self, conversation_id: str) -> list[ArtifactOut]:
        root = self._local_artifact_root(conversation_id)
        if not root.exists():
            return []
        artifacts: list[ArtifactOut] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not _is_excluded_artifact_dir(dirname)
            ]
            for filename in filenames:
                path = Path(dirpath) / filename
                relative_path = path.relative_to(root).as_posix()
                if not self._is_downloadable_artifact(relative_path):
                    continue
                stat = path.stat()
                artifacts.append(
                    ArtifactOut(
                        name=path.name,
                        path=relative_path,
                        relative_path=relative_path,
                        type=_artifact_type(relative_path),
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                    )
                )
        return sorted(artifacts, key=_artifact_sort_key)

    def _local_artifact_root(self, conversation_id: str) -> Path:
        return self.settings.artifact_storage_dir / conversation_id

    def _resolve_local_artifact_path(self, conversation_id: str, path: str) -> Path:
        root = self._local_artifact_root(conversation_id).resolve()
        relative = PurePosixPath(path)
        if path.startswith("/"):
            parts = PurePosixPath(path).parts
            workspace_parts = PurePosixPath(self.settings.sandbox_workspace).parts
            if parts[: len(workspace_parts)] == workspace_parts:
                relative = PurePosixPath(*parts[len(workspace_parts) :])
            else:
                relative = PurePosixPath(PurePosixPath(path).name)
        if relative.is_absolute() or ".." in relative.parts:
            raise AgentRuntimeError("Invalid artifact path")
        candidate = (root / Path(*relative.parts)).resolve()
        if root != candidate and root not in candidate.parents:
            raise AgentRuntimeError("Invalid artifact path")
        return candidate

    def _is_uploaded_input_artifact_path(self, path: str) -> bool:
        relative = self._workspace_relative_path(path)
        return is_uploaded_input_path(self.settings, relative)

    def _is_downloadable_artifact(self, relative_path: str) -> bool:
        return _is_candidate_artifact(relative_path) and not is_uploaded_input_path(
            self.settings,
            relative_path,
        )

    def _workspace_relative_path(self, path: str) -> str:
        relative = PurePosixPath(path)
        if path.startswith("/"):
            parts = PurePosixPath(path).parts
            workspace_parts = PurePosixPath(self.settings.sandbox_workspace).parts
            if parts[: len(workspace_parts)] == workspace_parts:
                relative = PurePosixPath(*parts[len(workspace_parts) :])
            else:
                relative = PurePosixPath(PurePosixPath(path).name)
        if relative.is_absolute() or ".." in relative.parts:
            raise AgentRuntimeError("Invalid artifact path")
        return relative.as_posix()

    def _build_artifact_bundle(self, conversation_id: str) -> ArtifactBundle | None:
        files = tuple(self._list_local_artifacts(conversation_id))
        if not files:
            return None

        digest = hashlib.sha256()
        byte_count = 0
        for artifact in files:
            local_path = self._resolve_local_artifact_path(
                conversation_id,
                artifact.relative_path,
            )
            digest.update(artifact.relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_file_sha256(local_path))
            digest.update(b"\0")
            byte_count += artifact.size

        sha256 = digest.hexdigest()
        root = self._local_artifact_root(conversation_id)
        bundle_root = root / ".rayue-bundles"
        bundle_root.mkdir(parents=True, exist_ok=True)
        archive_path = bundle_root / f"artifacts-{sha256[:16]}.tar"
        if not archive_path.exists():
            temporary_path = bundle_root / "artifacts.tmp.tar"
            temporary_path.unlink(missing_ok=True)
            with tarfile.open(temporary_path, "w") as archive:
                for artifact in files:
                    local_path = self._resolve_local_artifact_path(
                        conversation_id,
                        artifact.relative_path,
                    )
                    archive.add(local_path, arcname=artifact.relative_path)
            temporary_path.replace(archive_path)

        for old_path in bundle_root.glob("artifacts-*.tar"):
            if old_path != archive_path:
                old_path.unlink(missing_ok=True)

        return ArtifactBundle(
            path=archive_path,
            sha256=sha256,
            file_count=len(files),
            byte_count=byte_count,
            files=files,
        )

    def _render_artifact_context(self, conversation_id: str) -> str:
        files = self._list_local_artifacts(conversation_id)
        if not files:
            return ""

        shown_files = files[:40]
        lines = [
            "",
            "",
            "Generated files from previous turns are available in the current workspace root.",
            "Use these conversation outputs when the user refers to prior generated files.",
            "Generated file paths:",
        ]
        for file in shown_files:
            lines.append(f"- `{file.relative_path}` ({_format_bytes(file.size)})")
        if len(files) > len(shown_files):
            lines.append(f"- ... {len(files) - len(shown_files)} more files omitted")
        return "\n".join(lines)

    async def _render_conversation_context(self, conversation_id: str, current_user_content: str) -> str:
        async with SessionLocal() as session:
            messages = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at)
                )
            ).scalars().all()
        if (
            messages
            and messages[-1].role == "user"
            and messages[-1].content.strip() == current_user_content.strip()
        ):
            messages = messages[:-1]
        if not messages:
            return ""

        lines = [
            "",
            "",
            "Conversation history before the current user message.",
            "Use this to resolve references to earlier turns; do not repeat it unless needed.",
        ]
        for message in messages[-20:]:
            role = "User" if message.role == "user" else "Assistant"
            content = message.content.strip()
            if len(content) > 2000:
                content = f"{content[:2000]}\n...[truncated]"
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def _persist_recovery_message(
        self,
        conversation_id: str,
        artifacts: list[ArtifactOut],
    ) -> None:
        item_id = f"recovered-{conversation_id}-{int(datetime.now(timezone.utc).timestamp())}"
        files = "\n".join(f"- `{artifact.relative_path}`" for artifact in artifacts[:8])
        text = "处理没有正常结束，但已保存已生成文件。"
        if files:
            text += f"\n\n可下载文件：\n{files}"
        await self.persist_assistant_message(conversation_id, item_id, text)
        await self.emit(conversation_id, "assistant_message", {"item_id": item_id, "text": text})

    async def _persist_completion_message(
        self,
        conversation_id: str,
        artifacts: list[ArtifactOut],
    ) -> None:
        item_id = f"completed-{conversation_id}-{int(datetime.now(timezone.utc).timestamp())}"
        files = "\n".join(f"- `{artifact.relative_path}`" for artifact in artifacts[:8])
        text = "已完成并保存生成文件。"
        if files:
            text += f"\n\n可下载文件：\n{files}"
        await self.persist_assistant_message(conversation_id, item_id, text)
        await self.emit(conversation_id, "assistant_message", {"item_id": item_id, "text": text})

    async def _has_assistant_after_current_user(
        self,
        conversation_id: str,
        current_user_content: str,
    ) -> bool:
        async with SessionLocal() as session:
            messages = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at)
                )
            ).scalars().all()
        current_user_created_at = None
        for message in reversed(messages):
            if message.role == "user" and message.content.strip() == current_user_content.strip():
                current_user_created_at = message.created_at
                break
        if current_user_created_at is None:
            return any(message.role == "assistant" for message in messages)
        return any(
            message.role == "assistant" and message.created_at > current_user_created_at
            for message in messages
        )

    async def _set_conversation_status(
        self, conversation_id: str, status: str, error: str | None = None
    ) -> None:
        async with SessionLocal() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation:
                conversation.status = status
                conversation.error = error
                conversation.updated_at = utcnow()
                await session.commit()
        await self.emit(conversation_id, "conversation_status", {"status": status, "error": error})

    async def _recover_interrupted_conversations(self) -> None:
        async with SessionLocal() as session:
            conversations = (
                await session.execute(
                    select(Conversation).where(
                        Conversation.status.in_(["queued", "running", "stopping"])
                    )
                )
            ).scalars().all()
            recovery_targets: list[tuple[str, str | None]] = []
            for conversation in conversations:
                turn = await session.scalar(
                    select(AgentTurn)
                    .where(
                        AgentTurn.conversation_id == conversation.id,
                        AgentTurn.status.in_(["queued", "running", "stopping"]),
                    )
                    .order_by(desc(AgentTurn.started_at))
                    .limit(1)
                )
                recovery_targets.append((conversation.id, turn.id if turn else None))
                conversation.status = "idle"
                conversation.error = None
                conversation.updated_at = utcnow()
                if turn:
                    turn.status = "recovered"
                    turn.phase = "recovered_after_restart"
                    turn.error = "Server restarted before the task emitted a terminal event."
                    turn.completed_at = utcnow()
                    turn.updated_at = utcnow()
                session.add(
                    AgentEvent(
                        conversation_id=conversation.id,
                        type="conversation_status",
                        payload={
                            "status": "idle",
                            "error": None,
                            "reason": "recovered",
                            "rayueTurnId": turn.id if turn else None,
                        },
                    )
                )
            if conversations:
                await session.commit()
        for conversation_id, turn_id in recovery_targets:
            if turn_id:
                self._active_turn_ids[conversation_id] = turn_id
            try:
                worker = await self._artifact_worker(conversation_id)
                artifacts = await self._persist_and_emit_artifacts(
                    conversation_id,
                    worker,
                    "startup_recovery",
                )
                await self.emit(
                    conversation_id,
                    "turn_recovered",
                    {
                        "message": "服务恢复后已结束上一次未完成任务，并保存可下载文件。",
                        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts[:20]],
                    },
                )
            except Exception:
                if turn_id:
                    await self.emit(
                        conversation_id,
                        "turn_recovered",
                        {"message": "服务恢复后已结束上一次未完成任务。", "artifacts": []},
                    )
            finally:
                if turn_id and self._active_turn_ids.get(conversation_id) == turn_id:
                    self._active_turn_ids.pop(conversation_id, None)

    def _new_worker(self, conversation_id: str) -> "BaseConversationWorker":
        if self.settings.agent_run_mode == "mock":
            return MockConversationWorker(conversation_id, self)
        return CodexAppServerWorker(conversation_id, self)

    async def _artifact_worker(self, conversation_id: str) -> "BaseConversationWorker":
        worker = self._workers.get(conversation_id)
        if worker is not None and not worker.closed:
            return worker

        async with SessionLocal() as session:
            conversation = await session.get(Conversation, conversation_id)
            if not conversation:
                raise AgentRuntimeError("Conversation not found")
            if not conversation.sandbox_id:
                raise AgentRuntimeError("No sandbox exists for this conversation yet")

            worker = self._new_worker(conversation_id)
            if not isinstance(worker, CodexAppServerWorker):
                self._workers[conversation_id] = worker
                return worker
            await worker.attach_for_artifacts(
                sandbox_id=conversation.sandbox_id,
                workspace_path=conversation.workspace_path or self.settings.sandbox_workspace,
            )
            self._workers[conversation_id] = worker
            return worker

    async def _sweep_idle_workers(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)
            for conversation_id, worker in list(self._workers.items()):
                if worker.has_active_turn:
                    worker.touch()
                    continue
                idle_seconds = (now - worker.last_activity_at).total_seconds()
                if idle_seconds >= self.settings.sandbox_idle_timeout_seconds:
                    await self._persist_and_emit_artifacts(conversation_id, worker, "idle_timeout")
                    await worker.close()
                    self._workers.pop(conversation_id, None)
                    await self.emit(conversation_id, "sandbox_closed", {"reason": "idle_timeout"})
                    await self._set_conversation_status(conversation_id, "idle")


class BaseConversationWorker:
    def __init__(self, conversation_id: str, runtime: AgentRuntime) -> None:
        self.conversation_id = conversation_id
        self.runtime = runtime
        self.last_activity_at = datetime.now(timezone.utc)
        self.closed = False

    async def send_message(self, content: str) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        self.closed = True

    def touch(self) -> None:
        self.last_activity_at = datetime.now(timezone.utc)

    @property
    def has_active_turn(self) -> bool:
        return False

    async def list_artifacts(self) -> list[ArtifactOut]:
        raise AgentRuntimeError("Artifacts are not available in this run mode")

    async def read_artifact(self, path: str) -> tuple[bytes, str]:
        raise AgentRuntimeError("Artifacts are not available in this run mode")

    async def persist_artifacts(self) -> list[ArtifactOut]:
        return []

    async def sync_uploaded_files(self) -> None:
        return

    async def sync_artifacts(self) -> None:
        return

    async def interrupt_current_turn(self) -> None:
        self.closed = True
        raise AgentTurnInterrupted()


class MockConversationWorker(BaseConversationWorker):
    async def send_message(self, content: str) -> None:
        self.touch()
        item_id = f"mock_{int(self.last_activity_at.timestamp())}"
        await self.runtime.emit(self.conversation_id, "turn_started", {"provider": "mock"})
        text = (
            "Mock agent is running because AGENT_RUN_MODE=mock. "
            f"I received: {content.strip()}"
        )
        for token in text.split(" "):
            await asyncio.sleep(0.03)
            await self.runtime.emit(
                self.conversation_id,
                "assistant_delta",
                {"item_id": item_id, "delta": token + " "},
            )
        await self.runtime.persist_assistant_message(self.conversation_id, item_id, text)
        await self.runtime.emit(self.conversation_id, "turn_completed", {"status": "completed"})


class CodexAppServerWorker(BaseConversationWorker):
    def __init__(self, conversation_id: str, runtime: AgentRuntime) -> None:
        super().__init__(conversation_id, runtime)
        self.sandbox = None
        self.command_handle = None
        self.codex_home: str | None = None
        self.workspace: str | None = None
        self.thread_id: str | None = None
        self._stdout_buffer = ""
        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self._assistant_buffers: dict[str, str] = {}
        self._agent_messages_by_turn: dict[str, list[tuple[str, str]]] = {}
        self._turn_waiters: dict[str, asyncio.Future] = {}
        self._completed_turns: set[str] = set()
        self._skills_fingerprint: str | None = None
        self._uploaded_files_fingerprint: str | None = None
        self._artifact_files_fingerprint: str | None = None
        self._active_turn_id: str | None = None
        self._interrupt_requested = False
        self._artifact_snapshot_task: asyncio.Task | None = None
        self._last_command_completed_at: datetime | None = None
        self._last_agent_message_completed_at: datetime | None = None
        self._last_progress_at = datetime.now(timezone.utc)
        self._last_progress_phase = "starting"

    @property
    def has_active_turn(self) -> bool:
        return self._active_turn_id is not None

    async def send_message(self, content: str) -> None:
        self.touch()
        await self._ensure_started()
        self._raise_if_interrupted()
        await self._sync_admin_skills()
        self._raise_if_interrupted()
        await self._sync_uploaded_files()
        self._raise_if_interrupted()
        await self._sync_artifacts()
        self._raise_if_interrupted()
        if self.thread_id is None:
            await self._start_thread()
        self._raise_if_interrupted()
        assert self.thread_id is not None
        assert self.workspace is not None
        result = await self._request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": content, "textElements": []}],
                "cwd": self.workspace,
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "externalSandbox", "networkAccess": "enabled"},
            },
            timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if turn_id:
            self._active_turn_id = turn_id
            self._mark_progress("model")
            self._last_command_completed_at = None
            self._last_agent_message_completed_at = None
            await self.runtime._update_active_turn(
                self.conversation_id,
                codex_turn_id=turn_id,
                phase="model",
            )
            try:
                turn_params = await self._wait_for_turn(turn_id)
            finally:
                self._active_turn_id = None
            completed_turn = turn_params.get("turn") if isinstance(turn_params, dict) else None
            if isinstance(completed_turn, dict) and completed_turn.get("status") == "interrupted":
                raise AgentTurnInterrupted()

    async def _wait_for_turn(self, turn_id: str) -> dict[str, Any]:
        if turn_id in self._completed_turns:
            return {"turn": {"id": turn_id, "status": "completed"}}
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._turn_waiters[turn_id] = future
        deadline = loop.time() + TURN_COMPLETION_TIMEOUT_SECONDS
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    if await self._recover_turn_after_timeout(turn_id, complete_on_artifacts=True):
                        return {"turn": {"id": turn_id, "status": "completed"}}
                    if await self._interrupt_and_recover_turn(turn_id, "turn_timeout"):
                        return {"turn": {"id": turn_id, "status": "completed"}}
                    raise AgentRuntimeError("任务长时间没有完成，已尝试保存已生成文件。")
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(future),
                        timeout=min(TURN_PROGRESS_HEARTBEAT_SECONDS, remaining),
                    )
                    return result if isinstance(result, dict) else {}
                except asyncio.TimeoutError:
                    self._raise_if_interrupted()
                    await self._emit_turn_watchdog(turn_id)
                    if self._should_recover_post_agent_message_silence():
                        if await self._recover_turn_after_timeout(turn_id):
                            return {"turn": {"id": turn_id, "status": "completed"}}
                        if await self._interrupt_and_recover_turn(turn_id, "agent_message_stalled"):
                            return {"turn": {"id": turn_id, "status": "completed"}}
                        raise AgentRuntimeError("最终回复没有收到完成确认，已尝试保存已生成文件。")
                    if self._should_recover_post_command_silence():
                        if await self._recover_turn_after_timeout(turn_id):
                            return {"turn": {"id": turn_id, "status": "completed"}}
                        if await self._interrupt_and_recover_turn(turn_id, "turn_stalled"):
                            return {"turn": {"id": turn_id, "status": "completed"}}
                        raise AgentRuntimeError("生成文件后模型长时间没有返回最终回复，已保存可下载文件。")
                    if self._should_recover_model_silence():
                        if await self._recover_turn_after_timeout(turn_id):
                            return {"turn": {"id": turn_id, "status": "completed"}}
                        if await self._interrupt_and_recover_turn(turn_id, "model_stalled"):
                            return {"turn": {"id": turn_id, "status": "completed"}}
                        raise AgentRuntimeError("任务长时间没有返回进展，已尝试保存已生成文件。")
        except asyncio.TimeoutError as exc:
            if await self._recover_turn_after_timeout(turn_id, complete_on_artifacts=True):
                return {"turn": {"id": turn_id, "status": "completed"}}
            if await self._interrupt_and_recover_turn(turn_id, "turn_timeout"):
                return {"turn": {"id": turn_id, "status": "completed"}}
            raise AgentRuntimeError("任务长时间没有完成，已尝试保存已生成文件。") from exc
        finally:
            self._turn_waiters.pop(turn_id, None)

    async def _emit_turn_watchdog(self, turn_id: str) -> None:
        now = datetime.now(timezone.utc)
        silence_seconds = max(0, int((now - self._last_progress_at).total_seconds()))
        phase = self._last_progress_phase
        if self._last_command_completed_at is not None:
            phase = "waiting_for_model"
        message_by_phase = {
            "command": "命令仍在运行，等待工具返回",
            "model": "正在等待模型返回",
            "waiting_for_model": "工具已返回，正在等待模型确认结果",
            "recovering": "任务没有继续返回，正在保存已生成文件",
            "reasoning": "正在整理下一步",
            "agent_message": "正在生成回复",
        }
        await self.runtime.emit(
            self.conversation_id,
            "turn_watchdog",
            {
                "turnId": turn_id,
                "phase": phase,
                "message": message_by_phase.get(phase, "正在等待任务继续返回"),
                "silenceSeconds": silence_seconds,
            },
        )

    def _mark_progress(self, phase: str | None = None) -> None:
        self._last_progress_at = datetime.now(timezone.utc)
        if phase is not None:
            self._last_progress_phase = phase

    def _should_recover_post_agent_message_silence(self) -> bool:
        if (
            self._last_agent_message_completed_at is None
            or self._last_progress_phase != "agent_message"
        ):
            return False
        silence_seconds = (
            datetime.now(timezone.utc) - self._last_progress_at
        ).total_seconds()
        return silence_seconds >= POST_AGENT_MESSAGE_SILENCE_RECOVERY_SECONDS

    def _should_recover_post_command_silence(self) -> bool:
        if self._last_command_completed_at is None:
            return False
        last_progress = max(self._last_command_completed_at, self._last_progress_at)
        silence_seconds = (
            datetime.now(timezone.utc) - last_progress
        ).total_seconds()
        return silence_seconds >= POST_COMMAND_SILENCE_RECOVERY_SECONDS

    def _should_recover_model_silence(self) -> bool:
        if self._last_command_completed_at is not None or self._last_progress_phase == "command":
            return False
        silence_seconds = (
            datetime.now(timezone.utc) - self._last_progress_at
        ).total_seconds()
        return silence_seconds >= MODEL_SILENCE_RECOVERY_SECONDS

    async def close(self) -> None:
        self.closed = True
        if self._artifact_snapshot_task is not None:
            self._artifact_snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._artifact_snapshot_task
        if self.command_handle is not None:
            with contextlib.suppress(Exception):
                await self.command_handle.kill()
            self.command_handle = None
        if self.sandbox is not None:
            with contextlib.suppress(Exception):
                await self.sandbox.kill()
            self.sandbox = None

    async def interrupt_current_turn(self) -> None:
        self._interrupt_requested = True
        if self.command_handle is None or self.sandbox is None or self.thread_id is None:
            await self.close()
            raise AgentTurnInterrupted()

        turn_id = self._active_turn_id or ""
        try:
            await self._request(
                "turn/interrupt",
                {"threadId": self.thread_id, "turnId": turn_id},
                timeout=TURN_INTERRUPT_TIMEOUT_SECONDS,
            )
        except CodexRpcError as exc:
            if "no active turn" not in str(exc).lower():
                raise
        except Exception:
            await self.close()
            raise AgentTurnInterrupted()

    def _raise_if_interrupted(self) -> None:
        if self._interrupt_requested or self.closed:
            raise AgentTurnInterrupted()

    async def attach_for_artifacts(self, sandbox_id: str, workspace_path: str) -> None:
        if not self.runtime.settings.e2b_api_key:
            raise AgentRuntimeError("E2B_API_KEY is required to reconnect to a sandbox")
        from e2b import AsyncSandbox

        self.sandbox = await AsyncSandbox.connect(
            sandbox_id,
            timeout=self.runtime.settings.sandbox_idle_timeout_seconds,
            api_key=self.runtime.settings.e2b_api_key,
            request_timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        self.workspace = workspace_path
        self.touch()
        self.closed = False

    async def list_artifacts(self) -> list[ArtifactOut]:
        if self.sandbox is None or self.workspace is None:
            raise AgentRuntimeError("Sandbox is not active; cannot list artifacts")
        result = await self.sandbox.commands.run(
            _remote_artifact_list_script(self.workspace),
            timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise AgentRuntimeError(result.stderr or result.error or "Could not read generated files")
        try:
            entries = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise AgentRuntimeError("Could not read generated files") from exc

        artifacts: list[ArtifactOut] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            relative_path = entry.get("relative_path")
            if not isinstance(relative_path, str) or not _is_candidate_artifact(relative_path):
                continue
            if is_uploaded_input_path(self.runtime.settings, relative_path):
                continue
            artifacts.append(
                ArtifactOut(
                    name=str(entry.get("name") or PurePosixPath(relative_path).name),
                    path=str(entry.get("path") or _resolve_workspace_path(self.workspace, relative_path)),
                    relative_path=relative_path,
                    type=_artifact_type(relative_path),
                    size=int(entry.get("size") or 0),
                    modified_at=datetime.fromtimestamp(
                        float(entry.get("modified_at") or 0),
                        timezone.utc,
                    ),
                )
            )
        return sorted(artifacts, key=_artifact_sort_key)

    async def read_artifact(self, path: str) -> tuple[bytes, str]:
        if self.sandbox is None or self.workspace is None:
            raise AgentRuntimeError("Sandbox is not active; cannot download artifacts")
        artifact_path = _resolve_workspace_path(self.workspace, path)
        relative_path = _relative_workspace_path(self.workspace, artifact_path)
        if is_uploaded_input_path(self.runtime.settings, relative_path):
            raise AgentRuntimeError("Uploaded input files are not downloadable artifacts")
        data = await self.sandbox.files.read(artifact_path, format="bytes")
        return bytes(data), PurePosixPath(artifact_path).name

    async def persist_artifacts(self) -> list[ArtifactOut]:
        if self.sandbox is None or self.workspace is None:
            return self.runtime._list_local_artifacts(self.conversation_id)

        artifacts = await self.list_artifacts()
        root = self.runtime._local_artifact_root(self.conversation_id)
        root.mkdir(parents=True, exist_ok=True)
        persisted: list[ArtifactOut] = []
        for artifact in artifacts:
            if artifact.size > self.runtime.settings.max_artifact_bytes:
                continue
            destination = self.runtime._resolve_local_artifact_path(
                self.conversation_id,
                artifact.relative_path,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            data, _filename = await self.read_artifact(artifact.path)
            destination.write_bytes(data)
            stat = destination.stat()
            persisted.append(
                ArtifactOut(
                    name=destination.name,
                    path=artifact.relative_path,
                    relative_path=artifact.relative_path,
                    type="file",
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                )
            )
        return sorted(persisted, key=lambda item: item.relative_path)

    async def _ensure_started(self) -> None:
        if self.command_handle is not None and not self.closed:
            return
        settings = self.runtime.settings
        if not settings.e2b_api_key:
            raise AgentRuntimeError("E2B_API_KEY is required when AGENT_RUN_MODE=e2b")
        if not settings.codex_api_key:
            raise AgentRuntimeError("CODEX_API_KEY is required when AGENT_RUN_MODE=e2b")
        gpt_image2_api_key = settings.gpt_image2_api_key or settings.codex_api_key
        sandbox_envs = _sandbox_envs(settings, gpt_image2_api_key)

        from e2b import AsyncSandbox

        await self.runtime.emit(self.conversation_id, "sandbox_starting", {"template": settings.e2b_template})
        self.sandbox = await AsyncSandbox.create(
            template=settings.e2b_template,
            timeout=settings.sandbox_idle_timeout_seconds,
            envs=sandbox_envs,
            allow_internet_access=settings.sandbox_allow_internet,
            api_key=settings.e2b_api_key,
            request_timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        self.touch()
        await self.runtime.emit(
            self.conversation_id,
            "sandbox_started",
            {"sandbox_id": self.sandbox.sandbox_id},
        )

        home = await self._detect_home()
        self.codex_home = f"{home}/.codex"
        self.workspace = settings.sandbox_workspace
        await self.sandbox.files.write(f"{self.codex_home}/config.toml", render_codex_config(settings))
        await self.sandbox.files.make_dir(self.workspace)
        await self._sync_admin_skills()
        await self._sync_uploaded_files()

        await self.runtime.update_codex_thread(
            self.conversation_id,
            sandbox_id=self.sandbox.sandbox_id,
            workspace_path=self.workspace,
        )

        self.command_handle = await self.sandbox.commands.run(
            "codex app-server --listen stdio://",
            background=True,
            stdin=True,
            timeout=0,
            cwd=self.workspace,
            envs={
                **sandbox_envs,
                "CODEX_HOME": self.codex_home,
                "LOG_FORMAT": "json",
                "RUST_LOG": "warn",
            },
            on_stdout=self._handle_stdout,
            on_stderr=self._handle_stderr,
        )
        await self.runtime.emit(
            self.conversation_id,
            "app_server_started",
            {"pid": self.command_handle.pid},
        )
        await self._initialize()

    async def _detect_home(self) -> str:
        assert self.sandbox is not None
        result = await self.sandbox.commands.run(
            "printf %s \"$HOME\"",
            timeout=SHORT_REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        home = result.stdout.strip()
        return home or "/home/user"

    async def _sync_admin_skills(self) -> None:
        if self.sandbox is None or self.codex_home is None:
            return
        bundle = build_skill_bundle(self.runtime.settings)
        if bundle.sha256 == self._skills_fingerprint:
            return

        skills_dir = f"{self.codex_home}/skills"
        marker_path = f"{skills_dir}/.rayue-agent-skills.sha256"
        quoted_marker = shlex.quote(marker_path)
        quoted_hash = shlex.quote(bundle.sha256)
        check = await self.sandbox.commands.run(
            f'if test "$(cat {quoted_marker} 2>/dev/null)" = {quoted_hash}; '
            'then printf yes; else printf no; fi',
            timeout=SHORT_REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        if check.stdout.strip() == "yes":
            self._skills_fingerprint = bundle.sha256
            await self.runtime.emit(
                self.conversation_id,
                "skills_synced",
                {
                    "count": bundle.skill_count,
                    "files": bundle.file_count,
                    "bytes": bundle.byte_count,
                    "hash": bundle.sha256[:12],
                    "mode": "preloaded",
                    "skills": list(bundle.skill_names),
                },
            )
            return

        remote_archive_path = f"/tmp/rayue-agent-admin-skills-{bundle.sha256[:16]}.tar.gz"
        await self.sandbox.files.write(
            remote_archive_path,
            bundle.path.read_bytes(),
            request_timeout=REMOTE_LONG_OPERATION_TIMEOUT_SECONDS,
            use_octet_stream=True,
        )
        extract = await self.sandbox.commands.run(
            "set -e; "
            f"rm -rf {shlex.quote(skills_dir)}; "
            f"mkdir -p {shlex.quote(skills_dir)}; "
            f"tar -xzf {shlex.quote(remote_archive_path)} -C {shlex.quote(skills_dir)}; "
            f"printf %s {quoted_hash} > {quoted_marker}",
            timeout=REMOTE_LONG_OPERATION_TIMEOUT_SECONDS,
        )
        if extract.exit_code != 0:
            raise AgentRuntimeError(extract.stderr or extract.error or "Failed to sync skills")

        self._skills_fingerprint = bundle.sha256
        await self.runtime.emit(
            self.conversation_id,
            "skills_synced",
            {
                "count": bundle.skill_count,
                "files": bundle.file_count,
                "bytes": bundle.byte_count,
                "archive_bytes": bundle.path.stat().st_size,
                "hash": bundle.sha256[:12],
                "mode": "archive",
                "skills": list(bundle.skill_names),
            },
        )

    async def sync_uploaded_files(self) -> None:
        await self._sync_uploaded_files()

    async def sync_artifacts(self) -> None:
        await self._sync_artifacts()

    async def _sync_uploaded_files(self) -> None:
        if self.sandbox is None or self.workspace is None:
            return
        bundle = build_upload_bundle(self.runtime.settings, self.conversation_id)
        if bundle.file_count == 0 or bundle.sha256 == getattr(self, "_uploaded_files_fingerprint", None):
            return

        input_dir = f"{self.workspace.rstrip('/')}/{self.runtime.settings.sandbox_inputs_dir}"
        marker_path = f"{input_dir}/{INPUT_MARKER_NAME}"
        quoted_marker = shlex.quote(marker_path)
        quoted_hash = shlex.quote(bundle.sha256)
        check = await self.sandbox.commands.run(
            f'if test "$(cat {quoted_marker} 2>/dev/null)" = {quoted_hash}; '
            'then printf yes; else printf no; fi',
            timeout=SHORT_REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        if check.stdout.strip() != "yes":
            remote_archive_path = f"/tmp/rayue-agent-inputs-{self.conversation_id}-{bundle.sha256[:16]}.tar"
            await self.sandbox.files.write(
                remote_archive_path,
                bundle.path.read_bytes(),
                request_timeout=REMOTE_LONG_OPERATION_TIMEOUT_SECONDS,
                use_octet_stream=True,
            )
            extract = await self.sandbox.commands.run(
                "set -e; "
                f"rm -rf {shlex.quote(input_dir)}; "
                f"mkdir -p {shlex.quote(input_dir)}; "
                f"tar -xf {shlex.quote(remote_archive_path)} -C {shlex.quote(input_dir)}; "
                f"printf %s {quoted_hash} > {quoted_marker}",
                timeout=REMOTE_LONG_OPERATION_TIMEOUT_SECONDS,
            )
            if extract.exit_code != 0:
                raise AgentRuntimeError(
                    extract.stderr or extract.error or "Failed to sync uploaded files"
                )

        self._uploaded_files_fingerprint = bundle.sha256
        await self.runtime.emit(
            self.conversation_id,
            "input_files_synced",
            {
                "count": bundle.file_count,
                "bytes": bundle.byte_count,
                "hash": bundle.sha256[:12],
                "mode": "archive" if check.stdout.strip() != "yes" else "preloaded",
                "files": [file.model_dump(mode="json") for file in bundle.files],
            },
        )

    async def _sync_artifacts(self) -> None:
        if self.sandbox is None or self.workspace is None:
            return
        bundle = self.runtime._build_artifact_bundle(self.conversation_id)
        if bundle is None or bundle.sha256 == self._artifact_files_fingerprint:
            return

        marker_path = f"{self.workspace.rstrip('/')}/{ARTIFACT_MARKER_NAME}"
        quoted_marker = shlex.quote(marker_path)
        quoted_hash = shlex.quote(bundle.sha256)
        check = await self.sandbox.commands.run(
            f'if test "$(cat {quoted_marker} 2>/dev/null)" = {quoted_hash}; '
            'then printf yes; else printf no; fi',
            timeout=SHORT_REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        mode = "preloaded"
        if check.stdout.strip() != "yes":
            mode = "archive"
            remote_archive_path = (
                f"/tmp/rayue-artifacts-{self.conversation_id}-{bundle.sha256[:16]}.tar"
            )
            await self.sandbox.files.write(
                remote_archive_path,
                bundle.path.read_bytes(),
                request_timeout=REMOTE_LONG_OPERATION_TIMEOUT_SECONDS,
                use_octet_stream=True,
            )
            extract = await self.sandbox.commands.run(
                "set -e; "
                f"mkdir -p {shlex.quote(self.workspace)}; "
                f"tar -xf {shlex.quote(remote_archive_path)} -C {shlex.quote(self.workspace)}; "
                f"printf %s {quoted_hash} > {quoted_marker}",
                timeout=REMOTE_LONG_OPERATION_TIMEOUT_SECONDS,
            )
            if extract.exit_code != 0:
                raise AgentRuntimeError(
                    extract.stderr or extract.error or "Failed to sync generated files"
                )

        self._artifact_files_fingerprint = bundle.sha256
        await self.runtime.emit(
            self.conversation_id,
            "artifacts_synced",
            {
                "count": bundle.file_count,
                "bytes": bundle.byte_count,
                "hash": bundle.sha256[:12],
                "mode": mode,
                "files": [file.model_dump(mode="json") for file in bundle.files[:20]],
            },
        )

    async def _initialize(self) -> None:
        await self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "rayue_agent",
                    "title": "Rayue Agent",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        await self._notify("initialized")
        await self.runtime.emit(self.conversation_id, "app_server_initialized", {})

    async def _start_thread(self) -> None:
        assert self.workspace is not None
        settings = self.runtime.settings
        result = await self._request(
            "thread/start",
            {
                "model": settings.model_name,
                "modelProvider": settings.model_provider_id,
                "cwd": self.workspace,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "serviceName": "rayue_agent",
                "ephemeral": False,
            },
            timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not thread_id:
            raise AgentRuntimeError("thread/start did not return a thread id")
        self.thread_id = thread_id
        await self.runtime.update_codex_thread(self.conversation_id, codex_thread_id=thread_id)
        await self.runtime.emit(self.conversation_id, "thread_started", {"thread_id": thread_id})

    async def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self.command_handle is None or self.sandbox is None:
            raise AgentRuntimeError("Codex app-server is not running")
        async with self._send_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[request_id] = future
            payload = {"method": method, "id": request_id, "params": params}
            await self.sandbox.commands.send_stdin(self.command_handle.pid, json.dumps(payload) + "\n")
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            raise CodexRpcError(response["error"])
        return response.get("result") or {}

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.command_handle is None or self.sandbox is None:
            raise AgentRuntimeError("Codex app-server is not running")
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self.sandbox.commands.send_stdin(self.command_handle.pid, json.dumps(payload) + "\n")

    async def _handle_stdout(self, chunk: str) -> None:
        self.touch()
        self._stdout_buffer += chunk
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                await self.runtime.emit(self.conversation_id, "app_server_stdout", {"line": line[-4000:]})
                continue
            await self._handle_jsonrpc_message(message)

    async def _handle_stderr(self, chunk: str) -> None:
        self.touch()
        if self.runtime.settings.log_codex_raw_events:
            await self.runtime.emit(self.conversation_id, "app_server_stderr", {"chunk": chunk[-4000:]})

    async def _handle_jsonrpc_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = message["id"]
            future = self._pending.get(request_id)
            if future and not future.done():
                future.set_result(message)
            return
        method = message.get("method")
        params = message.get("params") or {}
        if not method:
            return

        if method == "item/agentMessage/delta":
            self._last_command_completed_at = None
            self._last_agent_message_completed_at = None
            self._mark_progress("agent_message")
            await self.runtime._update_active_turn(self.conversation_id, phase="agent_message")
            item_id = params.get("itemId")
            delta = params.get("delta") or ""
            if item_id:
                self._assistant_buffers[item_id] = self._assistant_buffers.get(item_id, "") + delta
            await self.runtime.emit(self.conversation_id, "assistant_delta", params)
            return

        if method in {"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"}:
            self._last_command_completed_at = None
            self._last_agent_message_completed_at = None
            self._mark_progress("reasoning")
            await self.runtime._update_active_turn(self.conversation_id, phase="reasoning")
            await self.runtime.emit(self.conversation_id, "reasoning_delta", {"method": method, **params})
            return

        if method == "item/completed":
            item = params.get("item") or {}
            item_id = item.get("id")
            turn_id = params.get("turnId")
            if isinstance(turn_id, str) and turn_id in self._completed_turns:
                if _completed_item_may_have_artifact(item):
                    self._schedule_artifact_snapshot()
                return
            self._mark_progress()
            if item.get("type") == "agentMessage" and item_id:
                self._last_command_completed_at = None
                self._last_agent_message_completed_at = datetime.now(timezone.utc)
                self._mark_progress("agent_message")
                await self.runtime._update_active_turn(self.conversation_id, phase="agent_message")
                text = item.get("text") or self._assistant_buffers.get(item_id, "")
                if turn_id and text:
                    self._remember_agent_message(turn_id, item_id, text)
            await self.runtime.emit(self.conversation_id, "item_completed", params)
            if item.get("type") == "commandExecution":
                self._last_command_completed_at = datetime.now(timezone.utc)
                self._last_agent_message_completed_at = None
                self._mark_progress("waiting_for_model")
                await self.runtime._update_active_turn(
                    self.conversation_id,
                    phase="waiting_for_model",
                    command_completed=True,
                )
            if _completed_item_may_have_artifact(item):
                self._schedule_artifact_snapshot()
            return

        event_type_by_method = {
            "turn/started": "turn_started",
            "turn/completed": "turn_completed",
            "turn/diff/updated": "diff_updated",
            "turn/plan/updated": "plan_updated",
            "item/started": "item_started",
            "thread/tokenUsage/updated": "token_usage",
            "warning": "warning",
            "configWarning": "config_warning",
        }
        event_type = event_type_by_method.get(method)
        if event_type:
            self._mark_progress()
            if method == "item/started":
                item = params.get("item") or {}
                if isinstance(item, dict):
                    if item.get("type") == "commandExecution":
                        self._last_command_completed_at = None
                        self._last_agent_message_completed_at = None
                        self._mark_progress("command")
                        await self.runtime._update_active_turn(
                            self.conversation_id,
                            phase="command",
                            command_started=True,
                        )
                    elif item.get("type") == "agentMessage":
                        self._last_command_completed_at = None
                        self._last_agent_message_completed_at = None
                        self._mark_progress("agent_message")
                        await self.runtime._update_active_turn(self.conversation_id, phase="agent_message")
                    elif item.get("type") == "reasoning":
                        self._last_command_completed_at = None
                        self._last_agent_message_completed_at = None
                        self._mark_progress("reasoning")
                        await self.runtime._update_active_turn(self.conversation_id, phase="reasoning")
            await self.runtime.emit(self.conversation_id, event_type, params)
            if method == "turn/completed":
                self._last_command_completed_at = None
                self._last_agent_message_completed_at = None
                self._mark_progress("completed")
                await self.runtime._update_active_turn(
                    self.conversation_id,
                    status="completed",
                    phase="completed",
                    completed=True,
                )
                turn = params.get("turn") or {}
                turn_id = turn.get("id")
                if turn_id:
                    self._remember_agent_messages_from_turn(turn_id, turn)
                    if not self._agent_messages_by_turn.get(turn_id):
                        await self._backfill_turn_messages(turn_id)
                    await self._emit_final_assistant_message(turn_id)
                await self._emit_artifact_summary("turn_completed")
                if turn_id:
                    self._completed_turns.add(turn_id)
                    future = self._turn_waiters.get(turn_id)
                    if future and not future.done():
                        future.set_result(params)
        elif self.runtime.settings.log_codex_raw_events:
            await self.runtime.emit(self.conversation_id, "raw_codex_event", message)

    async def _emit_artifact_summary(self, reason: str) -> list[ArtifactOut]:
        try:
            artifacts = await self.persist_artifacts()
        except Exception:
            return []
        await self.runtime.emit_artifacts_updated(self.conversation_id, artifacts, reason)
        return artifacts

    async def _emit_final_assistant_message(self, turn_id: str) -> None:
        messages = self._agent_messages_by_turn.pop(turn_id, [])
        if not messages:
            return
        item_id, text = messages[-1]
        await self.runtime.persist_assistant_message(self.conversation_id, item_id, text)
        await self.runtime.emit(
            self.conversation_id,
            "assistant_message",
            {"item_id": item_id, "text": text},
        )

    def _remember_agent_message(self, turn_id: str, item_id: str, text: str) -> None:
        messages = self._agent_messages_by_turn.setdefault(turn_id, [])
        if not any(existing_item_id == item_id for existing_item_id, _text in messages):
            messages.append((item_id, text))

    def _remember_agent_messages_from_turn(self, turn_id: str, turn: dict[str, Any]) -> bool:
        items = turn.get("items")
        if not isinstance(items, list):
            return False
        recovered = False
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("type") != "agentMessage":
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            item_id = item.get("id") if isinstance(item.get("id"), str) else f"{turn_id}-message-{index}"
            self._remember_agent_message(turn_id, item_id, text)
            recovered = True
        return recovered

    async def _backfill_turn_messages(self, turn_id: str) -> bool:
        if self.thread_id is None:
            return False
        try:
            result = await self._request(
                "thread/read",
                {"threadId": self.thread_id, "includeTurns": True},
                timeout=BACKFILL_TIMEOUT_SECONDS,
            )
        except Exception:
            return False

        thread = result.get("thread") if isinstance(result, dict) else None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if not isinstance(turns, list):
            return False

        for turn in turns:
            if isinstance(turn, dict) and turn.get("id") == turn_id:
                return self._remember_agent_messages_from_turn(turn_id, turn)
        return False

    async def _recover_turn_after_timeout(
        self,
        turn_id: str,
        *,
        complete_on_artifacts: bool = False,
    ) -> bool:
        if self.thread_id is None:
            return False
        try:
            result = await self._request(
                "thread/read",
                {"threadId": self.thread_id, "includeTurns": True},
                timeout=TURN_RECOVERY_READ_TIMEOUT_SECONDS,
            )
        except Exception:
            return False

        thread = result.get("thread") if isinstance(result, dict) else None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if not isinstance(turns, list):
            return False

        recovered_message = False
        recovered_completed = False
        for turn in turns:
            if not isinstance(turn, dict) or turn.get("id") != turn_id:
                continue
            recovered_completed = turn.get("status") == "completed"
            for index, item in enumerate(turn.get("items") or []):
                if not isinstance(item, dict) or item.get("type") != "agentMessage":
                    continue
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                item_id = item.get("id") if isinstance(item.get("id"), str) else f"{turn_id}-message-{index}"
                await self.runtime.persist_assistant_message(self.conversation_id, item_id, text)
                await self.runtime.emit(
                    self.conversation_id,
                    "assistant_message",
                    {"item_id": item_id, "text": text},
                )
                recovered_message = True
            break

        artifacts = await self._emit_artifact_summary("turn_recovered")
        if recovered_message or recovered_completed or (complete_on_artifacts and artifacts):
            self._completed_turns.add(turn_id)
            await self.runtime._update_active_turn(
                self.conversation_id,
                status="recovered" if not recovered_completed else "completed",
                phase="recovered",
                artifact_count=len(artifacts),
                completed=True,
            )
            await self.runtime.emit(
                self.conversation_id,
                "turn_recovered",
                {
                    "message": "已恢复任务结果并保存生成文件。",
                    "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts[:20]],
                },
            )
            return True
        return False

    async def _interrupt_and_recover_turn(self, turn_id: str, reason: str) -> bool:
        if self.thread_id is not None:
            try:
                await self._request(
                    "turn/interrupt",
                    {"threadId": self.thread_id, "turnId": turn_id},
                    timeout=TURN_INTERRUPT_TIMEOUT_SECONDS,
                )
            except CodexRpcError as exc:
                if "no active turn" not in str(exc).lower():
                    await self.runtime.emit(
                        self.conversation_id,
                        "turn_watchdog",
                        {
                            "turnId": turn_id,
                            "phase": "recovering",
                            "message": "任务没有继续返回，正在保存已生成文件",
                            "reason": reason,
                        },
                    )
            except Exception:
                await self.runtime.emit(
                    self.conversation_id,
                    "turn_watchdog",
                    {
                        "turnId": turn_id,
                        "phase": "recovering",
                        "message": "任务没有继续返回，正在保存已生成文件",
                        "reason": reason,
                    },
                )

        waiter = self._turn_waiters.get(turn_id)
        if waiter is not None and not waiter.done():
            with contextlib.suppress(asyncio.TimeoutError):
                result = await asyncio.wait_for(
                    asyncio.shield(waiter),
                    timeout=TURN_INTERRUPT_TIMEOUT_SECONDS,
                )
                if isinstance(result, dict):
                    return True

        artifacts = await self._emit_artifact_summary(reason)
        with contextlib.suppress(Exception):
            await self.close()
        if not artifacts:
            return False
        self._completed_turns.add(turn_id)
        await self.runtime._update_active_turn(
            self.conversation_id,
            status="recovered",
            phase="recovered",
            artifact_count=len(artifacts),
            completed=True,
        )
        await self.runtime.emit(
            self.conversation_id,
            "turn_recovered",
            {
                "message": "任务没有正常结束，已停止后台执行并保存生成文件。",
                "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts[:20]],
                "reason": reason,
            },
        )
        return True

    def _schedule_artifact_snapshot(self) -> None:
        if self._artifact_snapshot_task is not None and not self._artifact_snapshot_task.done():
            return
        self._artifact_snapshot_task = asyncio.create_task(self._delayed_artifact_snapshot())

    async def _delayed_artifact_snapshot(self) -> None:
        await asyncio.sleep(0.75)
        await self._emit_artifact_summary("command_completed")


def _remote_artifact_list_script(workspace: str) -> str:
    script = f"""
import json
import os
from pathlib import Path

root = Path({workspace!r})
excluded_dirs = set({sorted(ARTIFACT_EXCLUDED_DIRS)!r})
excluded_files = set({sorted(ARTIFACT_EXCLUDED_FILES)!r})
extensions = tuple({ARTIFACT_EXTENSIONS!r})
items = []

if root.exists():
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in excluded_dirs and not dirname.startswith(".")
        ]
        for filename in filenames:
            if filename in excluded_files or filename.startswith("."):
                continue
            if not filename.lower().endswith(extensions):
                continue
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
                relative_path = path.relative_to(root).as_posix()
            except OSError:
                continue
            items.append({{
                "name": filename,
                "path": path.as_posix(),
                "relative_path": relative_path,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            }})

print(json.dumps(items, ensure_ascii=False))
""".strip()
    return f"python - <<'PY'\n{script}\nPY"


def _is_excluded_artifact_dir(dirname: str) -> bool:
    return dirname in ARTIFACT_EXCLUDED_DIRS or dirname.startswith(".")


def _is_candidate_artifact(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts:
        return False
    if any(_is_excluded_artifact_dir(part) for part in parts[:-1]):
        return False
    name = parts[-1]
    if name in ARTIFACT_EXCLUDED_FILES or name.startswith("."):
        return False
    return name.lower().endswith(ARTIFACT_EXTENSIONS)


def _artifact_type(relative_path: str) -> str:
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


def _artifact_sort_key(artifact: ArtifactOut) -> tuple[int, str]:
    suffix = PurePosixPath(artifact.relative_path).suffix.lower()
    try:
        priority = ARTIFACT_PRIORITY_EXTENSIONS.index(suffix)
    except ValueError:
        priority = len(ARTIFACT_PRIORITY_EXTENSIONS)
    return priority, artifact.relative_path.lower()


def _file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def _format_bytes(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count / 1024 / 1024:.1f} MB"


def _relative_workspace_path(workspace: str, path: str) -> str:
    prefix = workspace.rstrip("/") + "/"
    if path == workspace:
        return "."
    if path.startswith(prefix):
        return path[len(prefix) :]
    return PurePosixPath(path).name


def _resolve_workspace_path(workspace: str, path: str) -> str:
    workspace = workspace.rstrip("/")
    if path.startswith("/"):
        normalized = str(PurePosixPath(path))
        if normalized == workspace or normalized.startswith(workspace + "/"):
            return normalized
        raise AgentRuntimeError("Artifact path must be inside the conversation workspace")

    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AgentRuntimeError("Invalid artifact path")
    return f"{workspace}/{relative}"


def _safe_user_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return "运行过程中断，已尝试保存已生成文件。"
    lower = message.lower()
    hidden_terms = ("codex", "e2b", "sandbox", "thread", "api_key", "model")
    if any(term in lower for term in hidden_terms):
        return "运行过程中断，已尝试保存已生成文件。"
    return message


def _sandbox_envs(settings: Settings, gpt_image2_api_key: str) -> dict[str, str]:
    envs = {
        "CODEX_API_KEY": settings.codex_api_key or "",
        "OPENAI_API_KEY": settings.codex_api_key or "",
        "GPT_IMAGE2_API_KEY": gpt_image2_api_key,
        "GPT_IMAGE2_BASE_URL": settings.gpt_image2_base_url,
        "GPT_IMAGE2_GENERATE_PATH": settings.gpt_image2_generate_path,
        "GPT_IMAGE2_EDIT_PATH": settings.gpt_image2_edit_path,
        "GPT_IMAGE2_TIMEOUT_SECONDS": str(settings.gpt_image2_timeout_seconds),
    }
    if settings.convertx_api_base_url:
        envs["RAYUE_CONVERTX_API_BASE_URL"] = settings.convertx_api_base_url.rstrip("/")
    return envs


def _completed_item_may_have_artifact(item: dict[str, Any]) -> bool:
    if item.get("type") != "commandExecution" or item.get("exitCode") != 0:
        return False
    text = " ".join(
        value
        for value in (item.get("command"), item.get("aggregatedOutput"))
        if isinstance(value, str)
    ).lower()
    return any(extension in text for extension in ARTIFACT_EXTENSIONS)
