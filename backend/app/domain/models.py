import uuid
from datetime import datetime
from datetime import timezone

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import BigInteger
from sqlalchemy import Integer
from sqlalchemy import LargeBinary
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("user"))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="Workspace.created_at",
    )
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailCode(Base):
    __tablename__ = "email_codes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("code"))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(String(24), default="login")
    salt: Mapped[str] = mapped_column(String(120), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailRateEvent(Base):
    __tablename__ = "email_rate_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rate"))
    email_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaptchaChallenge(Base):
    __tablename__ = "captcha_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cap"))
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    salt: Mapped[str] = mapped_column(String(120), nullable=False)
    answer_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    image_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ws"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120), default="我的工作区")
    limit_bytes: Mapped[int] = mapped_column(BigInteger, default=1024 * 1024 * 1024)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="workspaces")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="workspace")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("conv"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), default="新对话")
    status: Mapped[str] = mapped_column(String(40), default="idle")
    sandbox_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    codex_thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User | None] = relationship(back_populates="conversations")
    workspace: Mapped[Workspace | None] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentEvent.created_at",
    )
    turns: Mapped[list["AgentTurn"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentTurn.started_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mentioned_files: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evt"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="events")


class AgentTurn(Base):
    __tablename__ = "agent_turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("turn"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    user_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    codex_turn_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    phase: Mapped[str] = mapped_column(String(60), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_command_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_command_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class ArtifactRecord(Base):
    __tablename__ = "artifact_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(1024))
    relative_path: Mapped[str] = mapped_column(String(1024))
    type: Mapped[str] = mapped_column(String(40), default="file")
    size: Mapped[int] = mapped_column(Integer, default=0)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkspaceFile(Base):
    __tablename__ = "workspace_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("wf"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(512))
    relative_path: Mapped[str] = mapped_column(String(1024))
    object_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


Index("ix_messages_conversation_created", Message.conversation_id, Message.created_at)
Index("ix_users_email", User.email)
Index("ix_user_sessions_user", UserSession.user_id)
Index("ix_email_codes_email_expires", EmailCode.email, EmailCode.expires_at)
Index("ix_email_rate_events_lookup", EmailRateEvent.email_hash, EmailRateEvent.sent_at)
Index("ix_email_rate_events_source_lookup", EmailRateEvent.source_hash, EmailRateEvent.sent_at)
Index("ix_captcha_challenges_source_created", CaptchaChallenge.source_hash, CaptchaChallenge.created_at)
Index("ix_captcha_challenges_expires", CaptchaChallenge.expires_at)
Index("ix_workspaces_user_created", Workspace.user_id, Workspace.created_at)
Index("ix_conversations_user_updated", Conversation.user_id, Conversation.updated_at)
Index("ix_conversations_workspace_updated", Conversation.workspace_id, Conversation.updated_at)
Index("ix_agent_events_conversation_created", AgentEvent.conversation_id, AgentEvent.created_at)
Index("ix_agent_turns_conversation_started", AgentTurn.conversation_id, AgentTurn.started_at)
Index("ix_agent_turns_conversation_status", AgentTurn.conversation_id, AgentTurn.status)
Index("ix_artifact_records_conversation_path", ArtifactRecord.conversation_id, ArtifactRecord.relative_path, unique=True)
Index("ix_artifact_records_workspace_path", ArtifactRecord.workspace_id, ArtifactRecord.relative_path, unique=True)
Index("ix_artifact_records_turn", ArtifactRecord.turn_id)
Index("ix_workspace_files_workspace_path", WorkspaceFile.workspace_id, WorkspaceFile.relative_path, unique=True)
Index("ix_workspace_files_workspace_kind", WorkspaceFile.workspace_id, WorkspaceFile.kind)
