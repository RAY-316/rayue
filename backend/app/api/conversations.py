import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import runtime
from app.core.database import get_session
from app.core.event_bus import event_bus
from app.domain.models import AgentEvent
from app.domain.models import AgentTurn
from app.domain.models import Conversation
from app.domain.models import Message
from app.domain.models import utcnow
from app.domain.schemas import AgentTurnOut
from app.domain.schemas import ConversationDetail
from app.domain.schemas import ConversationOut
from app.domain.schemas import CreateConversationRequest
from app.domain.schemas import EventOut
from app.domain.schemas import MessageOut
from app.domain.schemas import SendMessageRequest
from app.domain.schemas import SendMessageResponse


router = APIRouter()


@router.get("/api/conversations", response_model=list[ConversationOut])
async def list_conversations(session: AsyncSession = Depends(get_session)) -> list[ConversationOut]:
    rows = await session.scalars(select(Conversation).order_by(desc(Conversation.updated_at)))
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("/api/conversations", response_model=ConversationOut)
async def create_conversation(
    body: CreateConversationRequest,
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    conversation = Conversation(title=body.title or "New conversation")
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> ConversationDetail:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
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
    session: AsyncSession = Depends(get_session),
) -> Response:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await runtime.delete_conversation(conversation_id)
    await session.delete(conversation)
    await session.commit()
    return Response(status_code=204)


@router.post("/api/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    session: AsyncSession = Depends(get_session),
) -> SendMessageResponse:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status in {"queued", "running", "stopping"}:
        raise HTTPException(status_code=409, detail="Current task is still running")
    message = Message(conversation_id=conversation_id, role="user", content=body.content)
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
    runtime.schedule_turn(conversation_id, body.content, turn.id)
    return SendMessageResponse(message=MessageOut.model_validate(message))


@router.post("/api/conversations/{conversation_id}/stop", status_code=204)
async def stop_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status not in {"queued", "running", "stopping"}:
        return Response(status_code=204)
    try:
        await runtime.stop_turn(conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/api/conversations/{conversation_id}/events")
async def stream_events(conversation_id: str) -> StreamingResponse:
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
