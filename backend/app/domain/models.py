import uuid
from datetime import datetime
from datetime import timezone

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
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


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("conv"))
    title: Mapped[str] = mapped_column(String(240), default="New conversation")
    status: Mapped[str] = mapped_column(String(40), default="idle")
    sandbox_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    codex_thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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


Index("ix_messages_conversation_created", Message.conversation_id, Message.created_at)
Index("ix_agent_events_conversation_created", AgentEvent.conversation_id, AgentEvent.created_at)
Index("ix_agent_turns_conversation_started", AgentTurn.conversation_id, AgentTurn.started_at)
Index("ix_agent_turns_conversation_status", AgentTurn.conversation_id, AgentTurn.status)
Index("ix_artifact_records_conversation_path", ArtifactRecord.conversation_id, ArtifactRecord.relative_path, unique=True)
Index("ix_artifact_records_turn", ArtifactRecord.turn_id)
