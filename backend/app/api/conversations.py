import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user_from_request
from app.auth import ensure_default_workspace
from app.auth import get_current_user
from app.api.state import runtime
from app.api.state import settings
from app.core.database import get_session
from app.core.event_bus import event_bus
from app.domain.models import AgentEvent
from app.domain.models import AgentTurn
from app.domain.models import Conversation
from app.domain.models import Message
from app.domain.models import User
from app.domain.models import Workspace
from app.domain.models import utcnow
from app.domain.schemas import AgentTurnOut
from app.domain.schemas import ConversationDetail
from app.domain.schemas import ConversationOut
from app.domain.schemas import CreateConversationRequest
from app.domain.schemas import EventOut
from app.domain.schemas import MessageOut
from app.domain.schemas import SendMessageRequest
from app.domain.schemas import SendMessageResponse
from app.files.workspace_index import WorkspaceFileIndexError
from app.files.workspace_index import render_mentioned_file_context
from app.files.workspace_index import resolve_file_mentions


router = APIRouter()


@router.get("/api/conversations", response_model=list[ConversationOut])
async def list_conversations(
    workspace_id: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    query = select(Conversation).where(Conversation.user_id == user.id)
    if workspace_id:
        workspace = await session.get(Workspace, workspace_id)
        if not workspace or workspace.user_id != user.id:
            raise HTTPException(status_code=404, detail="Workspace not found")
        query = query.where(Conversation.workspace_id == workspace_id)
    rows = await session.scalars(query.order_by(desc(Conversation.updated_at)))
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("/api/conversations", response_model=ConversationOut)
async def create_conversation(
    body: CreateConversationRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    if body.workspace_id:
        workspace = await session.get(Workspace, body.workspace_id)
        if not workspace or workspace.user_id != user.id:
            raise HTTPException(status_code=404, detail="Workspace not found")
    else:
        workspace = await ensure_default_workspace(session, settings, user)
    conversation = Conversation(
        title=body.title or "New conversation",
        user_id=user.id,
        workspace_id=workspace.id,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetail:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    events = await session.scalars(
        select(AgentEvent)
        .where(AgentEvent.conversation_id == conversation_id)
        .order_by(AgentEvent.created_at)
    )
    turns = await session.scalars(
        select(AgentTurn)
        .where(AgentTurn.conversation_id == conversation_id)
        .order_by(AgentTurn.started_at)
    )
    return ConversationDetail(
        conversation=ConversationOut.model_validate(conversation),
        messages=[MessageOut.model_validate(message) for message in messages],
        events=[EventOut.model_validate(event) for event in events],
        turns=[AgentTurnOut.model_validate(turn) for turn in turns],
    )


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await runtime.delete_conversation(conversation_id)
    await session.delete(conversation)
    await session.commit()
    return Response(status_code=204)


@router.post("/api/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SendMessageResponse:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status in {"queued", "running", "stopping"}:
        raise HTTPException(status_code=409, detail="Current task is still running")
    if conversation.workspace_id:
        active_workspace_turns = await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.workspace_id == conversation.workspace_id,
                Conversation.id != conversation_id,
                Conversation.status.in_(["queued", "running", "stopping"]),
            )
        )
        if int(active_workspace_turns or 0) > 0:
            raise HTTPException(status_code=409, detail="同一工作区已有任务在运行，请等待完成后再发送")
    try:
        mentioned_files = await resolve_file_mentions(
            session,
            settings,
            conversation.workspace_id or conversation_id,
            body.mentioned_files,
        )
    except WorkspaceFileIndexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mentioned_payload = [file.model_dump(mode="json") for file in mentioned_files]
    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=body.content,
        mentioned_files=mentioned_payload,
    )
    conversation.title = conversation.title if conversation.title != "New conversation" else body.content[:80]
    conversation.status = "queued"
    conversation.last_activity_at = utcnow()
    conversation.updated_at = utcnow()
    session.add(message)
    await session.flush()
    turn = AgentTurn(
        conversation_id=conversation_id,
        user_message_id=message.id,
        status="queued",
        phase="queued",
        last_event_at=utcnow(),
    )
    session.add(turn)
    await session.commit()
    await session.refresh(message)
    runtime.schedule_turn(
        conversation_id,
        body.content + render_mentioned_file_context(mentioned_files),
        turn.id,
    )
    return SendMessageResponse(message=MessageOut.model_validate(message))


@router.post("/api/conversations/{conversation_id}/stop", status_code=204)
async def stop_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status not in {"queued", "running", "stopping"}:
        return Response(status_code=204)
    try:
        await runtime.stop_turn(conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/api/conversations/{conversation_id}/events")
async def stream_events(
    conversation_id: str,
    request: Request,
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    user = await current_user_from_request(request, session=session, token=token)
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_stream() -> AsyncIterator[str]:
        async for event in event_bus.subscribe(conversation_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
