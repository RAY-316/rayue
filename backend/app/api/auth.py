from datetime import datetime

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import authenticate_password
from app.auth import bearer_token
from app.auth import create_email_code
from app.auth import delete_session
from app.auth import get_current_user
from app.auth import register_with_email_code
from app.auth import reset_password_with_email_code
from app.auth import verify_email_code
from app.core.database import get_session
from app.domain.models import User
from app.domain.schemas import AuthResponse
from app.domain.schemas import AuthStartRequest
from app.domain.schemas import AuthVerifyRequest
from app.domain.schemas import CaptchaChallengeResponse
from app.domain.schemas import EmailCodeResponse
from app.domain.schemas import PasswordLoginRequest
from app.domain.schemas import PasswordRegisterCompleteRequest
from app.domain.schemas import PasswordRegisterStartRequest
from app.domain.schemas import PasswordResetCompleteRequest
from app.domain.schemas import PasswordResetStartRequest
from app.domain.schemas import UserOut
from app.services.captcha import create_captcha_challenge
from app.services.captcha import request_source_key
from app.services.captcha import verify_captcha
from app.services.email_delivery import send_login_code_email

from .state import settings


router = APIRouter()


@router.get("/api/auth/captcha", response_model=CaptchaChallengeResponse)
async def captcha(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CaptchaChallengeResponse:
    captcha_id, image_data_url, expires_in_seconds = await create_captcha_challenge(
        session,
        settings,
        source_key=request_source_key(request),
    )
    return CaptchaChallengeResponse(
        captcha_id=captcha_id,
        image_data_url=image_data_url,
        expires_in_seconds=expires_in_seconds,
    )


@router.post("/api/auth/email/start", response_model=EmailCodeResponse)
async def start_email_login(
    payload: AuthStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> EmailCodeResponse:
    await _verify_captcha_for_email_send(session, payload.captcha_id, payload.captcha_answer)
    code = await create_email_code(
        session,
        settings,
        email=payload.email,
        display_name=payload.display_name,
        source_key=request_source_key(request),
    )
    send_login_code_email(settings=settings, to_email=payload.email.strip().lower(), code=code)
    return _email_code_response(code)


@router.post("/api/auth/email/verify", response_model=AuthResponse)
async def complete_email_login(
    payload: AuthVerifyRequest,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    user, token, expires_at = await verify_email_code(
        session,
        settings,
        email=payload.email,
        code=payload.code,
        force=payload.force,
    )
    return _auth_response(user, token, expires_at)


@router.post("/api/auth/register/start", response_model=EmailCodeResponse)
async def start_password_register(
    payload: PasswordRegisterStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> EmailCodeResponse:
    await _verify_captcha_for_email_send(session, payload.captcha_id, payload.captcha_answer)
    code = await create_email_code(
        session,
        settings,
        email=payload.email,
        display_name=payload.display_name,
        purpose="register",
        source_key=request_source_key(request),
    )
    send_login_code_email(settings=settings, to_email=payload.email.strip().lower(), code=code)
    return _email_code_response(code)


@router.post("/api/auth/register/complete", response_model=AuthResponse)
async def complete_password_register(
    payload: PasswordRegisterCompleteRequest,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    user, token, expires_at = await register_with_email_code(
        session,
        settings,
        email=payload.email,
        code=payload.code,
        password=payload.password,
        display_name=payload.display_name,
    )
    return _auth_response(user, token, expires_at)


@router.post("/api/auth/password/login", response_model=AuthResponse)
async def login_with_password(
    payload: PasswordLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    user, token, expires_at = await authenticate_password(
        session,
        settings,
        email=payload.email,
        password=payload.password,
        force=payload.force,
    )
    return _auth_response(user, token, expires_at)


@router.post("/api/auth/password/reset/start", response_model=EmailCodeResponse)
async def start_password_reset(
    payload: PasswordResetStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> EmailCodeResponse:
    await _verify_captcha_for_email_send(session, payload.captcha_id, payload.captcha_answer)
    code = await create_email_code(
        session,
        settings,
        email=payload.email,
        purpose="reset",
        source_key=request_source_key(request),
    )
    send_login_code_email(settings=settings, to_email=payload.email.strip().lower(), code=code)
    return _email_code_response(code)


@router.post("/api/auth/password/reset/complete", response_model=AuthResponse)
async def complete_password_reset(
    payload: PasswordResetCompleteRequest,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    user, token, expires_at = await reset_password_with_email_code(
        session,
        settings,
        email=payload.email,
        code=payload.code,
        password=payload.password,
    )
    return _auth_response(user, token, expires_at)


@router.get("/api/auth/me")
async def me(user: User = Depends(get_current_user)) -> dict[str, UserOut]:
    return {"user": UserOut.model_validate(user)}


@router.post("/api/auth/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await delete_session(session, bearer_token(request))
    return {"ok": True}


def _email_code_response(code: str) -> EmailCodeResponse:
    return EmailCodeResponse(
        expires_in_seconds=settings.email_code_ttl_minutes * 60,
        code=code if settings.email_delivery_mode == "mock" else None,
    )


def _auth_response(user: User, token: str, expires_at: datetime) -> AuthResponse:
    return AuthResponse(user=UserOut.model_validate(user), token=token, expires_at=expires_at)


async def _verify_captcha_for_email_send(
    session: AsyncSession,
    captcha_id: str | None,
    captcha_answer: str | None,
) -> None:
    await verify_captcha(
        session,
        settings,
        captcha_id=captcha_id,
        answer=captcha_answer,
    )
