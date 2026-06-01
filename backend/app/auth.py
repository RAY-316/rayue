from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime
from datetime import timedelta
from math import ceil

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from sqlalchemy import delete
from sqlalchemy import desc
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import get_settings
from app.core.database import get_session
from app.domain.models import EmailCode
from app.domain.models import EmailRateEvent
from app.domain.models import User
from app.domain.models import UserSession
from app.domain.models import Workspace
from app.domain.models import utcnow


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_CODE_PURPOSES = {"login", "register", "reset"}
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or not EMAIL_RE.match(normalized):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid email")
    return normalized


def validate_password(password: str) -> str:
    clean_password = str(password or "")
    if len(clean_password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters",
        )
    if len(clean_password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password must be {PASSWORD_MAX_LENGTH} characters or fewer",
        )
    if not clean_password.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password is required")
    return clean_password


def normalize_purpose(purpose: str | None) -> str:
    normalized = str(purpose or "login").strip().lower()
    if normalized not in EMAIL_CODE_PURPOSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid email code purpose")
    return normalized


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return _password_hasher().hash(validate_password(password))


def verify_password_hash(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        from argon2.exceptions import Argon2Error
    except ImportError as exc:
        raise RuntimeError("argon2-cffi is required for password authentication") from exc

    try:
        return bool(_password_hasher().verify(password_hash, str(password or "")))
    except Argon2Error:
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return bool(_password_hasher().check_needs_rehash(password_hash))


def _password_hasher():
    try:
        from argon2 import PasswordHasher
    except ImportError as exc:
        raise RuntimeError("argon2-cffi is required for password authentication") from exc

    return PasswordHasher()


async def create_email_code(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    display_name: str | None = None,
    purpose: str | None = "login",
    source_key: str | None = None,
) -> str:
    normalized_email = normalize_email(email)
    normalized_purpose = normalize_purpose(purpose)
    await _reserve_email_send(session, settings, normalized_email, source_key=source_key)
    await session.execute(
        delete(EmailCode).where(
            EmailCode.email == normalized_email,
            EmailCode.purpose == normalized_purpose,
            EmailCode.consumed_at.is_(None),
        )
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_urlsafe(16)
    session.add(
        EmailCode(
            email=normalized_email,
            display_name=(display_name or "").strip()[:120],
            purpose=normalized_purpose,
            salt=salt,
            code_hash=sha256(f"{salt}:{code}"),
            expires_at=utcnow() + timedelta(minutes=settings.email_code_ttl_minutes),
        )
    )
    await session.commit()
    return code


async def verify_email_code(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    code: str,
    purpose: str | None = "login",
    force: bool = False,
) -> tuple[User, str, datetime]:
    normalized_email = normalize_email(email)
    normalized_purpose = normalize_purpose(purpose)
    email_code = await _consume_email_code(session, normalized_email, code, normalized_purpose, consume=False)
    user = await get_user_by_email(session, normalized_email)
    if user is not None:
        await ensure_login_allowed(session, user.id, force=force)
    display_name = email_code.display_name or normalized_email.split("@")[0]
    user = await upsert_user(session, email=normalized_email, display_name=display_name)
    email_code.consumed_at = utcnow()
    token, expires_at = await create_session(session, settings, user.id)
    await ensure_default_workspace(session, settings, user)
    await session.commit()
    return user, token, expires_at


async def register_with_email_code(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    code: str,
    password: str,
    display_name: str | None = None,
) -> tuple[User, str, datetime]:
    clean_password = validate_password(password)
    normalized_email = normalize_email(email)
    email_code = await _consume_email_code(session, normalized_email, code, "register")
    user = await upsert_user(
        session,
        email=normalized_email,
        display_name=(display_name or email_code.display_name or normalized_email.split("@")[0]),
    )
    user.password_hash = hash_password(clean_password)
    user.updated_at = utcnow()
    await delete_sessions_for_user(session, user.id)
    token, expires_at = await create_session(session, settings, user.id)
    await ensure_default_workspace(session, settings, user)
    await session.commit()
    return user, token, expires_at


async def authenticate_password(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    force: bool = False,
) -> tuple[User, str, datetime]:
    user = await get_user_by_email(session, normalize_email(email))
    if user is None or not verify_password_hash(user.password_hash, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    await ensure_login_allowed(session, user.id, force=force)
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        user.updated_at = utcnow()
    token, expires_at = await create_session(session, settings, user.id)
    await ensure_default_workspace(session, settings, user)
    await session.commit()
    return user, token, expires_at


async def reset_password_with_email_code(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    code: str,
    password: str,
) -> tuple[User, str, datetime]:
    clean_password = validate_password(password)
    user = await get_user_by_email(session, normalize_email(email))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    await _consume_email_code(session, user.email, code, "reset")
    user.password_hash = hash_password(clean_password)
    user.updated_at = utcnow()
    await delete_sessions_for_user(session, user.id)
    token, expires_at = await create_session(session, settings, user.id)
    await ensure_default_workspace(session, settings, user)
    await session.commit()
    return user, token, expires_at


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    normalized_email = normalize_email(email)
    return await session.scalar(select(User).where(func.lower(User.email) == normalized_email))


async def upsert_user(session: AsyncSession, *, email: str, display_name: str) -> User:
    user = await get_user_by_email(session, email)
    if user is not None:
        if not user.display_name:
            user.display_name = display_name[:120]
        user.updated_at = utcnow()
        return user
    user = User(email=email, display_name=(display_name or email.split("@")[0])[:120])
    session.add(user)
    await session.flush()
    return user


async def create_session(session: AsyncSession, settings: Settings, user_id: str) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(days=settings.session_ttl_days)
    session.add(UserSession(token_hash=sha256(raw_token), user_id=user_id, expires_at=expires_at))
    return raw_token, expires_at


async def ensure_login_allowed(session: AsyncSession, user_id: str, *, force: bool) -> None:
    await delete_expired_sessions(session)
    existing = await session.scalar(select(UserSession).where(UserSession.user_id == user_id).limit(1))
    if existing is None:
        return
    if not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "active_session_exists",
                "message": "账号已在其他地方登录",
                "requires_force": True,
            },
        )
    await delete_sessions_for_user(session, user_id)


async def delete_sessions_for_user(session: AsyncSession, user_id: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.user_id == user_id))


async def delete_session(session: AsyncSession, raw_token: str | None) -> None:
    if raw_token:
        await session.execute(delete(UserSession).where(UserSession.token_hash == sha256(raw_token)))
        await session.commit()


async def delete_expired_sessions(session: AsyncSession) -> None:
    await session.execute(delete(UserSession).where(UserSession.expires_at < utcnow()))


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    return await current_user_from_request(request, session=session)


async def current_user_from_request(
    request: Request,
    *,
    session: AsyncSession,
    token: str | None = None,
) -> User:
    raw_token = token or bearer_token(request)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    await delete_expired_sessions(session)
    user_session = await session.get(UserSession, sha256(raw_token))
    if user_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await session.get(User, user_session.user_id)
    if user is None:
        await session.delete(user_session)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return user


def bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    query_token = request.query_params.get("token")
    return query_token.strip() if query_token else None


async def ensure_default_workspace(session: AsyncSession, settings: Settings, user: User) -> Workspace:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.created_at).limit(1)
    )
    if workspace is not None:
        return workspace
    workspace = Workspace(user_id=user.id, name="默认工作区", limit_bytes=settings.workspace_limit_bytes)
    session.add(workspace)
    await session.flush()
    return workspace


async def _reserve_email_send(
    session: AsyncSession,
    settings: Settings,
    email: str,
    *,
    source_key: str | None = None,
) -> None:
    now = utcnow()
    window_start = now - timedelta(hours=1)
    email_hash = sha256(email)
    rows = (
        await session.execute(
            select(EmailRateEvent)
            .where(EmailRateEvent.email_hash == email_hash, EmailRateEvent.sent_at >= window_start)
            .order_by(desc(EmailRateEvent.sent_at))
        )
    ).scalars().all()
    if rows:
        seconds_since_last = (now - rows[0].sent_at).total_seconds()
        if seconds_since_last < settings.email_code_cooldown_seconds:
            retry_after = max(1, ceil(settings.email_code_cooldown_seconds - seconds_since_last))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": "Please wait before requesting another login code", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
    if len(rows) >= settings.email_code_hourly_limit:
        retry_after = max(1, ceil((rows[-1].sent_at + timedelta(hours=1) - now).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": "Hourly login code limit reached", "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    source_hash = sha256(source_key) if source_key else None
    if source_hash:
        source_rows = (
            await session.execute(
                select(EmailRateEvent)
                .where(EmailRateEvent.source_hash == source_hash, EmailRateEvent.sent_at >= window_start)
                .order_by(desc(EmailRateEvent.sent_at))
            )
        ).scalars().all()
        if len(source_rows) >= settings.email_code_source_hourly_limit:
            retry_after = max(1, ceil((source_rows[-1].sent_at + timedelta(hours=1) - now).total_seconds()))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": "Too many email code requests", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
    session.add(EmailRateEvent(email_hash=email_hash, source_hash=source_hash, sent_at=now))


async def _consume_email_code(
    session: AsyncSession,
    email: str,
    code: str,
    purpose: str,
    *,
    consume: bool = True,
) -> EmailCode:
    clean_code = code.strip()
    if not re.match(r"^\d{6}$", clean_code):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid code")
    email_code = await session.scalar(
        select(EmailCode)
        .where(EmailCode.email == email, EmailCode.purpose == purpose)
        .order_by(desc(EmailCode.expires_at))
        .limit(1)
    )
    if email_code is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login code not found")
    if email_code.consumed_at:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Login code already used")
    if email_code.expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Login code expired")
    email_code.attempts += 1
    await session.flush()
    if email_code.attempts > 5:
        await session.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts")
    if not secrets.compare_digest(email_code.code_hash, sha256(f"{email_code.salt}:{clean_code}")):
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect code")
    if consume:
        email_code.consumed_at = utcnow()
    return email_code


def auth_settings() -> Settings:
    return get_settings()
