from __future__ import annotations

import base64
import secrets
import struct
import zlib
from datetime import timedelta

from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from sqlalchemy import delete
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sha256
from app.core.config import Settings
from app.domain.models import CaptchaChallenge
from app.domain.models import utcnow


CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PNG_WIDTH = 160
PNG_HEIGHT = 58

GLYPHS: dict[str, tuple[str, ...]] = {
    "2": ("1110", "0001", "0001", "0010", "0100", "1000", "1111"),
    "3": ("1110", "0001", "0001", "0110", "0001", "0001", "1110"),
    "4": ("1001", "1001", "1001", "1111", "0001", "0001", "0001"),
    "5": ("1111", "1000", "1000", "1110", "0001", "0001", "1110"),
    "6": ("0111", "1000", "1000", "1110", "1001", "1001", "0110"),
    "7": ("1111", "0001", "0010", "0010", "0100", "0100", "0100"),
    "8": ("0110", "1001", "1001", "0110", "1001", "1001", "0110"),
    "9": ("0110", "1001", "1001", "0111", "0001", "0001", "1110"),
    "A": ("0110", "1001", "1001", "1111", "1001", "1001", "1001"),
    "B": ("1110", "1001", "1001", "1110", "1001", "1001", "1110"),
    "C": ("0111", "1000", "1000", "1000", "1000", "1000", "0111"),
    "D": ("1110", "1001", "1001", "1001", "1001", "1001", "1110"),
    "E": ("1111", "1000", "1000", "1110", "1000", "1000", "1111"),
    "F": ("1111", "1000", "1000", "1110", "1000", "1000", "1000"),
    "G": ("0111", "1000", "1000", "1011", "1001", "1001", "0111"),
    "H": ("1001", "1001", "1001", "1111", "1001", "1001", "1001"),
    "J": ("0011", "0001", "0001", "0001", "1001", "1001", "0110"),
    "K": ("1001", "1010", "1100", "1100", "1010", "1001", "1001"),
    "L": ("1000", "1000", "1000", "1000", "1000", "1000", "1111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("1001", "1101", "1011", "1011", "1001", "1001", "1001"),
    "P": ("1110", "1001", "1001", "1110", "1000", "1000", "1000"),
    "Q": ("0110", "1001", "1001", "1001", "1011", "1001", "0111"),
    "R": ("1110", "1001", "1001", "1110", "1010", "1001", "1001"),
    "S": ("0111", "1000", "1000", "0110", "0001", "0001", "1110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("1001", "1001", "1001", "1001", "1001", "1001", "0110"),
    "V": ("10001", "10001", "10001", "01010", "01010", "00100", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("1001", "1001", "0110", "0110", "0110", "1001", "1001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("1111", "0001", "0010", "0010", "0100", "1000", "1111"),
}


async def create_captcha_challenge(
    session: AsyncSession,
    settings: Settings,
    *,
    source_key: str | None,
) -> tuple[str, str, int]:
    now = utcnow()
    await session.execute(delete(CaptchaChallenge).where(CaptchaChallenge.expires_at < now))
    source_hash = sha256(source_key) if source_key else None
    if source_hash:
        window_start = now - timedelta(hours=1)
        count = await session.scalar(
            select(func.count(CaptchaChallenge.id)).where(
                CaptchaChallenge.source_hash == source_hash,
                CaptchaChallenge.created_at >= window_start,
            )
        )
        if count is not None and count >= settings.captcha_source_hourly_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Captcha request limit reached",
            )

    length = min(8, max(4, settings.captcha_length))
    answer = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(length))
    salt = secrets.token_urlsafe(16)
    image_bytes = render_captcha_png(answer)
    challenge = CaptchaChallenge(
        source_hash=source_hash,
        salt=salt,
        answer_hash=sha256(f"{salt}:{answer}"),
        image_bytes=image_bytes,
        expires_at=now + timedelta(seconds=settings.captcha_ttl_seconds),
    )
    session.add(challenge)
    await session.commit()
    data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    return challenge.id, data_url, settings.captcha_ttl_seconds


async def verify_captcha(
    session: AsyncSession,
    settings: Settings,
    *,
    captcha_id: str | None,
    answer: str | None,
) -> None:
    clean_id = (captcha_id or "").strip()
    clean_answer = "".join(ch for ch in (answer or "").strip().upper() if ch.isalnum())
    if not clean_id or not clean_answer:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Captcha is required")
    challenge = await session.get(CaptchaChallenge, clean_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Captcha not found")
    if challenge.consumed_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Captcha already used")
    if challenge.expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Captcha expired")
    challenge.attempts += 1
    await session.flush()
    if challenge.attempts > settings.captcha_attempt_limit:
        await session.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many captcha attempts")
    if not secrets.compare_digest(challenge.answer_hash, sha256(f"{challenge.salt}:{clean_answer}")):
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect captcha")
    challenge.consumed_at = utcnow()


def request_source_key(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip = forwarded_for.split(",", 1)[0].strip()
    if not ip and request.client is not None:
        ip = request.client.host or ""
    return ip or None


def render_captcha_png(answer: str) -> bytes:
    pixels = bytearray([248, 250, 252] * PNG_WIDTH * PNG_HEIGHT)
    rng = secrets.SystemRandom()
    for _ in range(900):
        x = rng.randrange(PNG_WIDTH)
        y = rng.randrange(PNG_HEIGHT)
        shade = rng.randrange(190, 242)
        _set_pixel(pixels, x, y, (shade, shade + rng.randrange(0, 8), min(255, shade + rng.randrange(4, 14))))

    for _ in range(5):
        color = (rng.randrange(90, 165), rng.randrange(105, 180), rng.randrange(120, 195))
        _draw_line(
            pixels,
            rng.randrange(0, PNG_WIDTH),
            rng.randrange(0, PNG_HEIGHT),
            rng.randrange(0, PNG_WIDTH),
            rng.randrange(0, PNG_HEIGHT),
            color,
        )

    x = 16
    for char in answer:
        glyph = GLYPHS[char]
        scale = rng.randrange(5, 7)
        y = rng.randrange(9, 16)
        color = (rng.randrange(30, 75), rng.randrange(55, 95), rng.randrange(80, 125))
        _draw_glyph(pixels, glyph, x, y, scale, color, rng)
        x += (len(glyph[0]) + 2) * scale + rng.randrange(3, 8)

    for _ in range(150):
        x = rng.randrange(PNG_WIDTH)
        y = rng.randrange(PNG_HEIGHT)
        _set_pixel(pixels, x, y, (rng.randrange(40, 170), rng.randrange(45, 170), rng.randrange(50, 170)))

    return _encode_png(PNG_WIDTH, PNG_HEIGHT, pixels)


def _draw_glyph(
    pixels: bytearray,
    glyph: tuple[str, ...],
    x: int,
    y: int,
    scale: int,
    color: tuple[int, int, int],
    rng: secrets.SystemRandom,
) -> None:
    for row_index, row in enumerate(glyph):
        row_shift = rng.randrange(-1, 2)
        for col_index, value in enumerate(row):
            if value != "1":
                continue
            block_x = x + col_index * scale + row_shift
            block_y = y + row_index * scale
            for dy in range(scale):
                for dx in range(scale):
                    if rng.random() < 0.08:
                        continue
                    _set_pixel(pixels, block_x + dx, block_y + dy, color)


def _draw_line(
    pixels: bytearray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _set_pixel(pixels, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _set_pixel(pixels: bytearray, x: int, y: int, color: tuple[int, int, int]) -> None:
    if not (0 <= x < PNG_WIDTH and 0 <= y < PNG_HEIGHT):
        return
    index = (y * PNG_WIDTH + x) * 3
    pixels[index : index + 3] = bytes(color)


def _encode_png(width: int, height: int, pixels: bytearray) -> bytes:
    raw = bytearray()
    row_bytes = width * 3
    for y in range(height):
        raw.append(0)
        start = y * row_bytes
        raw.extend(pixels[start : start + row_bytes])
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )
