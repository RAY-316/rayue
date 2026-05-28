from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    sandbox_id: str | None
    codex_thread_id: str | None
    workspace_path: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    item_id: str | None
    created_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    type: str
    payload: dict
    created_at: datetime


class AgentTurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    user_message_id: str | None
    status: str
    phase: str
    error: str | None
    artifact_count: int
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    events: list[EventOut]
    turns: list[AgentTurnOut]


class CreateConversationRequest(BaseModel):
    title: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class SendMessageResponse(BaseModel):
    message: MessageOut


class SkillOut(BaseModel):
    name: str
    path: str
    bytes: int
    updated_at: datetime


class UpsertSkillRequest(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,80}$")
    content: str = Field(min_length=1)


class ArtifactOut(BaseModel):
    name: str
    path: str
    relative_path: str
    type: str
    size: int
    modified_at: datetime | None
    turn_id: str | None = None
    first_seen_at: datetime | None = None


class UploadedFileOut(BaseModel):
    name: str
    path: str
    relative_path: str
    size: int
    modified_at: datetime | None
