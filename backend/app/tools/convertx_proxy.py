import asyncio
import json
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import Response

from app.core.config import Settings
from app.core.config import get_settings


router = APIRouter(prefix="/api/tools/convertx", tags=["tools"])

DOWNLOAD_LINK_RE = re.compile(r'href="([^"]*/download/[^"]+)"')
TARGET_RE = re.compile(r'data-value="([^",]+),([^"]+)"')
VIDEO_FORMATS = {"3g2", "3gp", "avi", "flv", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "ogv", "webm", "wmv"}
AUDIO_FORMATS = {"aac", "aiff", "alac", "flac", "m4a", "mka", "mp3", "ogg", "opus", "wav", "wma"}
OFFICE_FORMATS = {
    "csv",
    "doc",
    "docm",
    "docx",
    "odp",
    "ods",
    "odt",
    "ppt",
    "pptm",
    "pptx",
    "rtf",
    "tsv",
    "xls",
    "xlsm",
    "xlsx",
}
IMAGE_FORMATS = {"avif", "bmp", "gif", "heic", "ico", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp"}


@router.get("/health")
async def convertx_health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(_service_url(settings, "/healthcheck"))
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="ConvertX service is not healthy")
    return {"status": "ok"}


@router.get("/targets")
async def convertx_targets(
    file_type: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    cleaned_type = _clean_extension(file_type)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        await _start_job(client, settings)
        response = await client.post(
            _service_url(settings, "/conversions"),
            data={"fileType": cleaned_type},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not read ConvertX targets")
    targets = _parse_targets(response.text)
    return {"file_type": cleaned_type, "targets": targets}


@router.post("/convert")
async def convertx_convert(
    file: UploadFile = File(...),
    target_format: str = Form(...),
    converter: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
) -> Response:
    filename = PurePosixPath(file.filename or "input").name
    if not filename or filename in {".", ".."}:
        filename = "input"
    target = _clean_extension(target_format)
    source_type = _clean_extension(PurePosixPath(filename).suffix)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    timeout_seconds = settings.convertx_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        job_id = await _start_job(client, settings)
        selected_converter = converter or await _select_converter(client, settings, source_type, target)
        if not selected_converter:
            raise HTTPException(
                status_code=400,
                detail=f"No ConvertX converter found for {source_type or 'unknown'} to {target}",
            )

        upload = await client.post(
            _service_url(settings, "/upload"),
            files={"file": (filename, data, file.content_type or "application/octet-stream")},
        )
        if upload.status_code >= 400:
            raise HTTPException(status_code=502, detail="ConvertX upload failed")

        convert = await client.post(
            _service_url(settings, "/convert"),
            data={
                "convert_to": f"{target},{selected_converter}",
                "file_names": json.dumps([filename], ensure_ascii=False),
            },
        )
        if convert.status_code not in {200, 302, 303}:
            raise HTTPException(status_code=502, detail="ConvertX conversion did not start")

        href, output_name = await _wait_for_result(client, settings, job_id, timeout_seconds)
        download = await client.get(urljoin(settings.convertx_service_url, href))
        if download.status_code != 200 or not download.content:
            raise HTTPException(status_code=502, detail="ConvertX download failed")

    ascii_output_name = output_name.encode("ascii", errors="ignore").decode("ascii")
    if not ascii_output_name or ascii_output_name.startswith("."):
        ascii_output_name = f"converted{PurePosixPath(output_name).suffix or '-file'}"
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_output_name}\"; filename*=UTF-8''{quote(output_name)}",
        "X-ConvertX-Converter": selected_converter,
    }
    return Response(content=download.content, media_type="application/octet-stream", headers=headers)


async def _start_job(client: httpx.AsyncClient, settings: Settings) -> str:
    response = await client.get(_service_url(settings, "/"))
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="ConvertX service did not start a job")
    job_id = client.cookies.get("jobId")
    if not job_id:
        raise HTTPException(
            status_code=502,
            detail="ConvertX did not create a job; check ALLOW_UNAUTHENTICATED and HTTP_ALLOWED",
        )
    return job_id


async def _select_converter(
    client: httpx.AsyncClient,
    settings: Settings,
    source_type: str,
    target: str,
) -> str | None:
    response = await client.post(
        _service_url(settings, "/conversions"),
        data={"fileType": source_type},
    )
    if response.status_code >= 400:
        return None
    candidates = [candidate for candidate in _parse_targets(response.text) if candidate["target"] == target]
    preferred = _preferred_converters(source_type, target)
    for converter in preferred:
        if any(candidate["converter"] == converter for candidate in candidates):
            return converter
    if candidates:
        return candidates[0]["converter"]
    return None


def _preferred_converters(source_type: str, target: str) -> list[str]:
    if source_type in VIDEO_FORMATS or target in VIDEO_FORMATS or source_type in AUDIO_FORMATS or target in AUDIO_FORMATS:
        return ["ffmpeg"]
    if source_type in OFFICE_FORMATS or target in OFFICE_FORMATS or target == "pdf":
        return ["libreoffice", "pandoc", "calibre"]
    if source_type in IMAGE_FORMATS or target in IMAGE_FORMATS:
        return ["imagemagick"]
    return []


async def _wait_for_result(
    client: httpx.AsyncClient,
    settings: Settings,
    job_id: str,
    timeout_seconds: int,
) -> tuple[str, str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.post(_service_url(settings, f"/progress/{job_id}"))
        if response.status_code == 404:
            raise HTTPException(status_code=502, detail="ConvertX job was not found")
        if response.status_code < 400:
            if "Failed, check logs" in response.text or "File type not supported" in response.text:
                raise HTTPException(status_code=422, detail="ConvertX could not convert this file")
            href = _extract_download_href(response.text)
            if href:
                output_name = PurePosixPath(href).name or "converted-file"
                return href, output_name
        await asyncio.sleep(1)
    raise HTTPException(status_code=504, detail="ConvertX conversion timed out")


def _parse_targets(html: str) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    targets: list[dict[str, str]] = []
    for target, converter in TARGET_RE.findall(html):
        key = (target, converter)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"target": target, "converter": converter})
    return targets


def _extract_download_href(html: str) -> str | None:
    for href in DOWNLOAD_LINK_RE.findall(html):
        if "/download/" in href:
            return href
    return None


def _clean_extension(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned.startswith("."):
        cleaned = cleaned[1:]
    if not cleaned or "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        raise HTTPException(status_code=400, detail="Invalid file extension")
    return cleaned


def _service_url(settings: Settings, path: str) -> str:
    return urljoin(settings.convertx_service_url.rstrip("/") + "/", path.lstrip("/"))
