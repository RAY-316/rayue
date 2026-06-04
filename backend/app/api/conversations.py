import json
import re
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
from app.domain.schemas import UpdateConversationRequest
from app.files.workspace_index import WorkspaceFileIndexError
from app.files.workspace_index import render_mentioned_file_context
from app.files.workspace_index import resolve_file_mentions


router = APIRouter()
DEFAULT_CONVERSATION_TITLE = "新对话"
DEFAULT_CONVERSATION_TITLES = {DEFAULT_CONVERSATION_TITLE, "New conversation", ""}
LEGACY_BUGGY_CONVERSATION_TITLES = {"PPT 图片比例修复"}
TITLE_MAX_LENGTH = 18


def _is_default_conversation_title(title: str | None) -> bool:
    return (title or "").strip() in DEFAULT_CONVERSATION_TITLES


def _should_autogenerate_conversation_title(title: str | None) -> bool:
    stripped = (title or "").strip()
    return stripped in DEFAULT_CONVERSATION_TITLES or stripped in LEGACY_BUGGY_CONVERSATION_TITLES


def _clean_title_source(content: str) -> str:
    text = re.sub(r"```.*?```", " ", content, flags=re.S)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"[/\\][\w./\\ -]+", " ", text)
    text = re.sub(r"\b(?:outputs|inputs|storage|work|tmp)\S*", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"^(帮我|麻烦|请|能不能|可以|看看|看下|分析一下|仔细分析|现在|另外|为啥|为什么|怎么|如何|这个|那个|我的)+",
        "",
        text,
    ).strip()
    return text


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _fallback_title(text: str) -> str:
    text = re.sub(r"[#*_`~|<>]", "", text)
    text = re.sub(r"[。！？!?；;，,：:]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return DEFAULT_CONVERSATION_TITLE
    words = text.split()
    if len(words) > 1 and all(ord(char) < 128 for char in text):
        title = " ".join(words[:4])
    else:
        title = text[:TITLE_MAX_LENGTH]
    return title.strip() or DEFAULT_CONVERSATION_TITLE


def _compact_title_fragment(text: str) -> str:
    text = re.sub(r"[\"'“”‘’#*_`~|<>]", " ", text)
    text = re.sub(r"[，。！？!?；;：:\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"^(帮我|麻烦|请|能不能|可以|看看|看下|做个|做一个|制作|生成|创建|写一个|关于|有关|主题是|主题为|一个|一份|一套)+",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.split(r"(?:pptx?|deck|slides?|幻灯片|演示文稿)", text, maxsplit=1, flags=re.I)[0].strip()
    text = re.sub(r"(的|相关|介绍|最好|需要|要求)$", "", text, flags=re.I).strip()
    return text


def _ppt_subject_title(text: str) -> str | None:
    subject = None
    topic_match = re.search(r"(?:关于|有关|主题是|主题为)\s*([^，。！？!?；;：:\n]{2,40})", text, flags=re.I)
    if topic_match:
        subject = topic_match.group(1)
    else:
        before_ppt = re.split(r"(?:pptx?|deck|slides?|幻灯片|演示文稿)", text, maxsplit=1, flags=re.I)[0]
        subject = before_ppt
    subject = _compact_title_fragment(subject or "")
    if not subject or subject.lower() in {"ppt", "pptx", "deck", "slide"}:
        return None
    subject = re.sub(r"\s*的$", "", subject).strip()
    if len(subject) < 2:
        return None
    return f"{subject[:12]} PPT"[:TITLE_MAX_LENGTH]


def _auto_conversation_title(content: str) -> str:
    text = _clean_title_source(content)
    if not text:
        return DEFAULT_CONVERSATION_TITLE

    if _contains_any(text, ("ppt", "pptx", "幻灯片", "演示文稿", "deck", "slide")):
        if _contains_any(text, ("压缩", "变形", "拉伸", "面包", "比例", "失真", "挤压", "挤成")):
            return "PPT 图片比例修复"
        if _contains_any(text, ("修改", "编辑", "调整", "优化", "修")):
            return "PPT 修改优化"
        return _ppt_subject_title(text) or "PPT 制作"

    if _contains_any(text, ("标题", "重命名", "新对话")):
        return "对话标题优化"

    if _contains_any(text, ("429", "key", "api key", "llm", "rate limit", "限流", "额度")):
        return "LLM 调用排查"

    if _contains_any(text, ("生图", "生成图片", "画图", "图片生成", "gpt image", "gpt-image")):
        return "图片生成"

    if _contains_any(text, ("前端", "页面", "按钮", "侧边栏", "ui", "样式", "渲染", "滚动")):
        if _contains_any(text, ("bug", "报错", "失败", "卡", "不显示", "问题")):
            return "前端问题排查"
        return "前端界面调整"

    if _contains_any(text, ("skill", "技能", "约束", "prompt")):
        return "Skill 约束优化"

    if _contains_any(text, ("新闻", "最新", "今天", "今日")):
        if _contains_any(text, ("科技", "ai", "人工智能")):
            return "科技新闻查询"
        return "新闻查询"

    if _contains_any(text, ("查询", "搜索", "查一下", "找一下")):
        return f"{_fallback_title(text).rstrip('查询搜索 ')}查询"[:TITLE_MAX_LENGTH]

    if _contains_any(text, ("bug", "报错", "失败", "卡住", "不显示", "异常", "问题")):
        return f"{_fallback_title(text).rstrip('问题排查 ')}问题排查"[:TITLE_MAX_LENGTH]

    if _contains_any(text, ("生成", "创建", "做一个", "写一个", "实现", "开发")):
        return f"{_fallback_title(text).rstrip('生成制作 ')}生成"[:TITLE_MAX_LENGTH]

    return _fallback_title(text)


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
        title=(body.title or DEFAULT_CONVERSATION_TITLE).strip()[:120] or DEFAULT_CONVERSATION_TITLE,
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


@router.patch("/api/conversations/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    conversation.title = title[:120]
    conversation.updated_at = utcnow()
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


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
    if _should_autogenerate_conversation_title(conversation.title):
        first_user_content = await session.scalar(
            select(Message.content)
            .where(Message.conversation_id == conversation_id, Message.role == "user")
            .order_by(Message.created_at)
            .limit(1)
        )
        conversation.title = _auto_conversation_title(first_user_content or body.content)
    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=body.content,
        mentioned_files=mentioned_payload,
    )
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
    await session.refresh(conversation)
    runtime.schedule_turn(
        conversation_id,
        body.content + render_mentioned_file_context(mentioned_files),
        turn.id,
    )
    return SendMessageResponse(
        message=MessageOut.model_validate(message),
        turn=AgentTurnOut.model_validate(turn),
        conversation=ConversationOut.model_validate(conversation),
    )


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
