from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    workspace_id: str | None = None
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
    mentioned_files: list[dict] = Field(default_factory=list)
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
    workspace_id: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    mentioned_files: list["FileMentionRequest"] = Field(default_factory=list)


class SendMessageResponse(BaseModel):
    message: MessageOut
    turn: AgentTurnOut
    conversation: ConversationOut


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


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    created_at: datetime
    updated_at: datetime


class AuthStartRequest(BaseModel):
    email: str
    display_name: str | None = None
    captcha_id: str | None = None
    captcha_answer: str | None = None


class AuthVerifyRequest(BaseModel):
    email: str
    code: str = Field(min_length=6, max_length=6)
    force: bool = False


class PasswordRegisterStartRequest(BaseModel):
    email: str
    display_name: str | None = None
    captcha_id: str | None = None
    captcha_answer: str | None = None


class PasswordRegisterCompleteRequest(BaseModel):
    email: str
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = None


class PasswordLoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1)
    force: bool = False


class PasswordResetStartRequest(BaseModel):
    email: str
    captcha_id: str | None = None
    captcha_answer: str | None = None


class PasswordResetCompleteRequest(BaseModel):
    email: str
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=8, max_length=128)


class AuthResponse(BaseModel):
    user: UserOut
    token: str
    expires_at: datetime


class EmailCodeResponse(BaseModel):
    ok: bool = True
    expires_in_seconds: int
    code: str | None = None


class CaptchaChallengeResponse(BaseModel):
    captcha_id: str
    image_data_url: str
    expires_in_seconds: int


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    limit_bytes: int
    used_bytes: int = 0
    available_bytes: int = 0
    created_at: datetime
    updated_at: datetime


class CreateWorkspaceRequest(BaseModel):
    name: str | None = None


class UpdateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceFileOut(BaseModel):
    id: str | None = None
    name: str
    path: str
    relative_path: str
    kind: str
    type: str
    size: int
    version: int = 1
    modified_at: datetime | None


class GrokOutputOut(BaseModel):
    name: str
    path: str
    media_type: str
    size: int
    url: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None


class GrokGenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    model: str
    prompt: str
    params: dict
    outputs: list[GrokOutputOut] = Field(default_factory=list)
    provider_request_id: str | None
    error: str | None
    usage: dict | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class GrokGenerationListOut(BaseModel):
    items: list[GrokGenerationOut]
    page: int
    page_size: int
    total: int


class GrokImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    aspect_ratio: str = "1:1"
    resolution: str = "1k"
    size: str = "auto"


class GrokVideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    model: str = "grok-imagine-video"
    seconds: int = Field(default=4, ge=1, le=15)
    aspect_ratio: str = "16:9"
    resolution: str = "480p"
    image_url: str | None = None


class FileMentionRequest(BaseModel):
    file_id: str | None = None
    relative_path: str | None = None
    version: int | None = None


class FileMentionOut(BaseModel):
    file_id: str
    name: str
    relative_path: str
    kind: str
    type: str
    size: int
    version: int
