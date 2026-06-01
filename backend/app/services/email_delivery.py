from __future__ import annotations

import hashlib
import hmac
import html
import json
import smtplib
import ssl
import time
from datetime import datetime
from datetime import timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any
from urllib import error
from urllib import request

from fastapi import HTTPException
from fastapi import status

from app.core.config import Settings


LOGIN_CODE_SUBJECT = "Rayue 登录验证码"


def send_login_code_email(*, settings: Settings, to_email: str, code: str) -> None:
    safe_code = html.escape(code)
    _send_email(
        settings=settings,
        to_email=to_email,
        subject=LOGIN_CODE_SUBJECT,
        html_body=(
            '<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;'
            'padding:24px;color:#111827;">'
            '<h2 style="margin:0 0 16px;">Sign in to Rayue</h2>'
            '<p style="margin:0 0 18px;">Use this code to finish signing in:</p>'
            f'<p style="font-size:32px;font-weight:700;letter-spacing:0.18em;margin:0 0 18px;">{safe_code}</p>'
            '<p style="margin:0;color:#6b7280;font-size:14px;">'
            f"This code expires in {settings.email_code_ttl_minutes} minutes. "
            "If you did not request it, you can ignore this email."
            "</p></div>"
        ),
        text_body=(
            "Sign in to Rayue\n\n"
            f"Your login code is: {code}\n\n"
            f"This code expires in {settings.email_code_ttl_minutes} minutes."
        ),
    )


def _send_email(
    *,
    settings: Settings,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    mode = settings.email_delivery_mode.strip().lower()
    if mode == "mock":
        return
    if mode == "resend":
        _send_resend_email(settings=settings, to_email=to_email, subject=subject, html_body=html_body, text_body=text_body)
        return
    if mode == "tencent_smtp":
        _send_tencent_smtp_email(settings=settings, to_email=to_email, subject=subject, html_body=html_body, text_body=text_body)
        return
    if mode == "tencent_api":
        _send_tencent_api_email(settings=settings, to_email=to_email, subject=subject, html_body=html_body, text_body=text_body)
        return
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unsupported email delivery mode")


def _send_resend_email(
    *,
    settings: Settings,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    if not settings.resend_api_key:
        raise HTTPException(status_code=status.HTTP_424_FAILED_DEPENDENCY, detail="RESEND_API_KEY is not configured")

    payload = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    data = json.dumps(payload).encode("utf-8")
    resend_request = request.Request(
        f"{settings.resend_api_base_url.rstrip('/')}/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Rayue/0.1 email client",
        },
        method="POST",
    )
    try:
        with request.urlopen(resend_request, timeout=20) as response:
            if 200 <= response.status < 300:
                return
            detail = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise HTTPException(status_code=status.HTTP_424_FAILED_DEPENDENCY, detail="Unable to deliver email") from exc
    raise HTTPException(status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=f"Unable to deliver email: {detail[:200]}")


def _send_tencent_smtp_email(
    *,
    settings: Settings,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    if not settings.tencent_smtp_username or not settings.tencent_smtp_password:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="Tencent SMTP credentials are not configured",
        )

    message = EmailMessage()
    message["From"] = formataddr(("Rayue", settings.tencent_smtp_from_email))
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            settings.tencent_smtp_host,
            settings.tencent_smtp_port,
            timeout=20,
            context=context,
        ) as smtp:
            smtp.login(settings.tencent_smtp_username, settings.tencent_smtp_password)
            smtp.send_message(message, from_addr=settings.tencent_smtp_from_email, to_addrs=[to_email])
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Unable to deliver email via Tencent SMTP: {type(exc).__name__}",
        ) from exc


def _send_tencent_api_email(
    *,
    settings: Settings,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    if settings.tencent_ses_template_id is None:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="Tencent SES template ID is not configured",
        )

    body: dict[str, Any] = {
        "FromEmailAddress": formataddr(("Rayue", settings.tencent_ses_from_email)),
        "Destination": [to_email],
        "Subject": subject,
        "Template": {
            "TemplateID": settings.tencent_ses_template_id,
            "TemplateData": json.dumps(
                {"code": _extract_login_code(text_body), "ttl": str(settings.email_code_ttl_minutes)},
                separators=(",", ":"),
            ),
        },
        "TriggerType": 1,
        "HeaderFrom": settings.tencent_ses_from_email,
    }
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    headers = _tencent_api_headers(settings=settings, action="SendEmail", payload=payload)
    api_request = request.Request(
        f"https://{settings.tencent_ses_endpoint}/",
        data=payload.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(api_request, timeout=20) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="Unable to deliver email via Tencent API",
        ) from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        parsed = {}
    response_data = parsed.get("Response") if isinstance(parsed, dict) else None
    if isinstance(response_data, dict) and "Error" not in response_data and response_data.get("MessageId"):
        return

    detail = response_body[:300] if response_body else "empty Tencent API response"
    raise HTTPException(
        status_code=status.HTTP_424_FAILED_DEPENDENCY,
        detail=f"Unable to deliver email via Tencent API: {detail}",
    )


def _tencent_api_headers(
    *,
    settings: Settings,
    action: str,
    payload: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    if not settings.tencent_ses_secret_id or not settings.tencent_ses_secret_key:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="Tencent Cloud API credentials are not configured",
        )

    service = "ses"
    host = settings.tencent_ses_endpoint
    content_type = "application/json; charset=utf-8"
    timestamp = timestamp or int(time.time())
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    canonical_headers = f"content-type:{content_type}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, hashed_payload]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = _tc3_sign(settings.tencent_ses_secret_key, date, service, string_to_sign)
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={settings.tencent_ses_secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": "2020-10-02",
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": settings.tencent_ses_region,
    }


def _tc3_sign(secret_key: str, date: str, service: str, string_to_sign: str) -> str:
    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, "tc3_request")
    return hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _extract_login_code(text_body: str) -> str:
    marker = "Your login code is:"
    if marker not in text_body:
        return ""
    return text_body.split(marker, 1)[1].strip().splitlines()[0].strip()
