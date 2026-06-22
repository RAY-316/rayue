import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy import desc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.api.state import settings
from app.core.database import get_session
from app.core.database import SessionLocal
from app.domain.models import GrokGeneration
from app.domain.models import User
from app.domain.models import utcnow
from app.domain.schemas import GrokGenerationOut
from app.domain.schemas import GrokGenerationListOut
from app.domain.schemas import GrokImageGenerateRequest


router = APIRouter()
logger = logging.getLogger(__name__)

GROK_IMAGE_MODELS = {"grok-imagine-image", "grok-imagine-image-quality"}
GPT_IMAGE_MODELS = {"gpt-image-2"}
IMAGE_MODELS = GROK_IMAGE_MODELS | GPT_IMAGE_MODELS
VIDEO_MODELS = {"grok-imagine-video", "grok-imagine-video-1.5-preview"}
VIDEO_MODES = {"text-to-video", "image-to-video", "reference-to-video", "video-extension", "video-edit"}
MAX_VIDEO_REFERENCE_IMAGES = 7
TALKING_PHOTO_MODEL = "talking-photo"
TALKING_PHOTO_DEFAULT_PROMPT = "A person talking"
IMAGE_ASPECT_RATIOS = {
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "auto",
}
IMAGE_RESOLUTIONS = {"1k", "2k"}
GPT_IMAGE_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}
VIDEO_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
VIDEO_RESOLUTIONS = {"480p", "720p"}
TALKING_PHOTO_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
TALKING_PHOTO_AUDIO_TYPES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac"}
_creation_tasks: set[asyncio.Task[None]] = set()
_creation_semaphore: asyncio.Semaphore | None = None


@router.get("/api/grok/generations", response_model=GrokGenerationListOut)
async def list_grok_generations(
    kind: str = Query("all"),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=20),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrokGenerationListOut:
    query = select(GrokGeneration).where(GrokGeneration.user_id == user.id)
    count_query = select(func.count()).select_from(GrokGeneration).where(GrokGeneration.user_id == user.id)
    if kind in {"image", "video"}:
        query = query.where(GrokGeneration.kind == kind)
        count_query = count_query.where(GrokGeneration.kind == kind)
    if status in {"queued", "running", "succeeded", "failed"}:
        query = query.where(GrokGeneration.status == status)
        count_query = count_query.where(GrokGeneration.status == status)
    total = int((await session.execute(count_query)).scalar_one())
    rows = (
        await session.execute(
            query.order_by(desc(GrokGeneration.created_at)).offset((page - 1) * page_size).limit(page_size),
        )
    ).scalars().all()
    return GrokGenerationListOut(
        items=[_generation_out(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/api/grok/images/generations", response_model=GrokGenerationOut)
async def generate_grok_image(
    body: GrokImageGenerateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrokGenerationOut:
    if body.model not in IMAGE_MODELS:
        raise HTTPException(status_code=400, detail="不支持的生图模型")
    _require_image_key(body.model)
    if _is_gpt_image_model(body.model):
        if body.size not in GPT_IMAGE_SIZES:
            raise HTTPException(status_code=400, detail="不支持的 GPT 图片尺寸")
    else:
        if body.aspect_ratio not in IMAGE_ASPECT_RATIOS:
            raise HTTPException(status_code=400, detail="不支持的图片比例")
        if body.resolution not in IMAGE_RESOLUTIONS:
            raise HTTPException(status_code=400, detail="不支持的图片分辨率")

    generation = GrokGeneration(
        user_id=user.id,
        kind="image",
        status="queued",
        model=body.model,
        prompt=body.prompt,
        params=_image_generation_params(body.model, body.n, body.aspect_ratio, body.resolution, body.size),
    )
    session.add(generation)
    await session.flush()
    await session.commit()
    await session.refresh(generation)
    _schedule_creation_job(
        _run_image_generation_job(
            generation.id,
            user.id,
            body.model,
            body.prompt,
            body.n,
            body.aspect_ratio,
            body.resolution,
            body.size,
        )
    )
    return _generation_out(generation)


@router.post("/api/grok/images/edits", response_model=GrokGenerationOut)
async def edit_grok_image(
    prompt: str = Form(min_length=1, max_length=8000),
    model: str = Form("gpt-image-2"),
    n: int = Form(1),
    aspect_ratio: str = Form("1:1"),
    resolution: str = Form("1k"),
    size: str = Form("auto"),
    image_urls: str | None = Form(None),
    source_refs: str | None = Form(None),
    images: list[UploadFile] | None = File(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrokGenerationOut:
    if model not in IMAGE_MODELS:
        raise HTTPException(status_code=400, detail="不支持的图生图模型")
    _require_image_key(model)
    if n < 1 or n > 4:
        raise HTTPException(status_code=400, detail="图片张数必须在 1 到 4 张之间")
    if images and len(images) > 3:
        raise HTTPException(status_code=400, detail="图生图最多支持 3 张输入图片")

    source_ref_values = _clean_source_refs(source_refs)
    reference_count = len(images or []) + len(_clean_image_urls(image_urls)) + len(source_ref_values)
    if reference_count == 0:
        raise HTTPException(status_code=400, detail="图生图需要至少 1 张输入图片")
    if reference_count > 3:
        raise HTTPException(status_code=400, detail="图生图最多支持 3 张输入图片")
    if _is_gpt_image_model(model):
        if size not in GPT_IMAGE_SIZES:
            raise HTTPException(status_code=400, detail="不支持的 GPT 图片尺寸")
        input_names = _input_image_names(images or [], image_urls) + await _source_image_names(session, user, source_ref_values)
    else:
        if aspect_ratio not in IMAGE_ASPECT_RATIOS:
            raise HTTPException(status_code=400, detail="不支持的图片比例")
        if resolution not in IMAGE_RESOLUTIONS:
            raise HTTPException(status_code=400, detail="不支持的图片分辨率")
        input_names = _input_image_names(images or [], image_urls) + await _source_image_names(session, user, source_ref_values)

    generation = GrokGeneration(
        user_id=user.id,
        kind="image",
        status="queued",
        model=model,
        prompt=prompt,
        params={
            "mode": "image-to-image",
            "n": n,
            **_image_edit_params(model, aspect_ratio, resolution, size),
            "reference_count": reference_count,
            "input_images": input_names,
        },
    )
    session.add(generation)
    await session.flush()
    input_paths = [
        *await _save_uploaded_image_inputs(user.id, generation.id, images or []),
        *await _save_source_image_inputs(session, user, user.id, generation.id, source_ref_values),
    ]
    await session.commit()
    await session.refresh(generation)
    _schedule_creation_job(
        _run_image_edit_job(
            generation.id,
            user.id,
            model,
            prompt,
            n,
            aspect_ratio,
            resolution,
            size,
            [path.as_posix() for path in input_paths],
            _clean_image_urls(image_urls),
        )
    )
    return _generation_out(generation)


@router.post("/api/grok/videos", response_model=GrokGenerationOut)
async def create_grok_video(
    prompt: str = Form(min_length=1, max_length=8000),
    mode: str = Form("image-to-video"),
    model: str = Form("grok-imagine-video"),
    seconds: int = Form(4),
    duration: int | None = Form(None),
    aspect_ratio: str = Form("16:9"),
    resolution: str = Form("480p"),
    image_url: str | None = Form(None),
    image_urls: str | None = Form(None),
    source_ref: str | None = Form(None),
    source_refs: str | None = Form(None),
    image: UploadFile | None = File(None),
    images: list[UploadFile] | None = File(None),
    video_url: str | None = Form(None),
    video_source_ref: str | None = Form(None),
    video: UploadFile | None = File(None),
    media_manifest: str | None = Form(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrokGenerationOut:
    _require_key()
    if mode not in VIDEO_MODES:
        raise HTTPException(status_code=400, detail="不支持的视频任务类型")
    if model not in VIDEO_MODELS:
        raise HTTPException(status_code=400, detail="不支持的视频模型")
    if seconds < 1 or seconds > 15:
        raise HTTPException(status_code=400, detail="视频时长必须在 1 到 15 秒之间")
    if aspect_ratio not in VIDEO_ASPECT_RATIOS:
        raise HTTPException(status_code=400, detail="不支持的视频比例")
    if resolution not in VIDEO_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="不支持的视频分辨率")
    if mode in {"reference-to-video", "video-extension", "video-edit"} and model != "grok-imagine-video":
        raise HTTPException(status_code=400, detail="该视频任务必须使用 grok-imagine-video")
    if mode == "text-to-video" and model != "grok-imagine-video":
        raise HTTPException(status_code=400, detail="文生视频必须使用 grok-imagine-video")
    if mode == "video-extension" and not 2 <= int(duration or seconds) <= 10:
        raise HTTPException(status_code=400, detail="视频扩展时长必须在 2 到 10 秒之间")

    generation = GrokGeneration(
        user_id=user.id,
        kind="video",
        status="queued",
        model=model,
        prompt=prompt,
        params={
            "mode": mode,
            "seconds": seconds,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        },
    )
    session.add(generation)
    await session.flush()

    media_manifest_items = _parse_media_manifest(media_manifest)
    image_uploads = [*(images or [])]
    if image is not None:
        image_uploads.insert(0, image)
    media_inputs = await _video_media_inputs(
        session,
        user,
        user.id,
        generation.id,
        media_manifest_items,
        image_uploads,
        video,
        image_urls=image_urls or image_url,
        source_refs=source_refs or source_ref,
        video_url=video_url,
        video_source_ref=video_source_ref,
    )
    _validate_video_media(mode, model, media_inputs)
    generation.params = {
        **generation.params,
        "media": media_inputs["labels"],
        "image_count": len(media_inputs["image_urls"]),
        "video_count": 1 if media_inputs["video_url"] else 0,
    }
    await session.commit()
    await session.refresh(generation)
    _schedule_creation_job(
        _run_video_start_job(
            generation.id,
            mode,
            model,
            prompt,
            seconds,
            duration,
            aspect_ratio,
            resolution,
            media_inputs["image_urls"],
            media_inputs["video_url"],
        )
    )
    return _generation_out(generation)


@router.post("/api/grok/talking-photo", response_model=GrokGenerationOut)
async def create_talking_photo(
    prompt: str = Form(""),
    image: UploadFile = File(...),
    audio: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrokGenerationOut:
    _require_talking_photo_config()
    clean_prompt = _talking_photo_prompt(prompt)
    generation = GrokGeneration(
        user_id=user.id,
        kind="video",
        status="queued",
        model=TALKING_PHOTO_MODEL,
        prompt=clean_prompt,
        params={"mode": "talking-photo"},
    )
    session.add(generation)
    await session.flush()
    image_path = await _save_talking_photo_input(user.id, generation.id, image, "image", 1)
    audio_path = await _save_talking_photo_input(user.id, generation.id, audio, "audio", 2)
    generation.params = {
        **generation.params,
        "image": image_path.name,
        "audio": audio_path.name,
        "max_audio_duration": 60,
        "fps": 25,
    }
    await session.commit()
    await session.refresh(generation)
    _schedule_creation_job(
        _run_talking_photo_start_job(
            generation.id,
            user.id,
            clean_prompt,
            image_path.as_posix(),
            audio_path.as_posix(),
        )
    )
    return _generation_out(generation)


@router.post("/api/grok/generations/{generation_id}/refresh", response_model=GrokGenerationOut)
async def refresh_grok_generation(
    generation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrokGenerationOut:
    generation = await _owned_generation(session, user, generation_id)
    if _is_talking_photo_generation(generation):
        return await _refresh_talking_photo_generation(session, user, generation)
    if generation.kind != "video":
        return _generation_out(generation)
    _require_key()
    if generation.status in {"succeeded", "failed"}:
        return _generation_out(generation)
    if not generation.provider_request_id and generation.status in {"queued", "running"}:
        return _generation_out(generation)
    if not generation.provider_request_id:
        generation.status = "failed"
        generation.error = "缺少视频任务 ID"
        await session.commit()
        await session.refresh(generation)
        return _generation_out(generation)

    try:
        data = await _get_json(f"/videos/{generation.provider_request_id}")
        generation.status = _normalize_video_status(data.get("status"))
        generation.params = {**(generation.params or {}), "progress": _number_or_none(data.get("progress"))}
        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        url = video.get("url") if isinstance(video, dict) else None
        if generation.status == "succeeded" and isinstance(url, str) and not generation.outputs:
            generation.outputs = [await _download_video_output(user.id, generation.id, url, video)]
            generation.completed_at = utcnow()
        elif generation.status == "failed":
            generation.error = _friendly_grok_error(data.get("error") or "视频生成失败")
    except HTTPException as exc:
        generation.error = _friendly_grok_error(exc.detail)
    except Exception as exc:
        generation.error = _friendly_grok_error(exc)

    generation.updated_at = utcnow()
    await session.commit()
    await session.refresh(generation)
    return _generation_out(generation)


@router.delete("/api/grok/generations/{generation_id}", status_code=204)
async def delete_grok_generation(
    generation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    generation = await _owned_generation(session, user, generation_id)
    output_dir = _output_dir(user.id, generation.id)
    legacy_output_dir = _legacy_output_dir(user.id, generation.id)
    await session.delete(generation)
    await session.commit()
    shutil.rmtree(output_dir, ignore_errors=True)
    shutil.rmtree(legacy_output_dir, ignore_errors=True)
    return Response(status_code=204)


@router.get("/api/grok/generations/{generation_id}/download")
async def download_grok_output(
    generation_id: str,
    index: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    generation = await _owned_generation(session, user, generation_id)
    outputs = generation.outputs or []
    if index >= len(outputs):
        raise HTTPException(status_code=404, detail="文件不存在")
    output = outputs[index]
    path = _existing_output_path(user.id, generation.id, output.get("name", "output"))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = output.get("media_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(
        content=path.read_bytes(),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


def _creation_job_semaphore() -> asyncio.Semaphore:
    global _creation_semaphore
    if _creation_semaphore is None:
        _creation_semaphore = asyncio.Semaphore(max(1, settings.creation_max_concurrent_jobs))
    return _creation_semaphore


def _schedule_creation_job(coro) -> None:
    task = asyncio.create_task(_run_scheduled_creation_job(coro))
    _creation_tasks.add(task)
    task.add_done_callback(_creation_tasks.discard)


async def _run_scheduled_creation_job(coro) -> None:
    async with _creation_job_semaphore():
        try:
            await coro
        except Exception:
            logger.exception("Creation job crashed")


async def _run_image_generation_job(
    generation_id: str,
    user_id: str,
    model: str,
    prompt: str,
    n: int,
    aspect_ratio: str,
    resolution: str,
    size: str,
) -> None:
    if not await _mark_generation_running(generation_id):
        return
    try:
        if _is_gpt_image_model(model):
            result = await _run_gpt_image_requests(
                generation_id,
                user_id,
                n,
                lambda: _post_gpt_json(
                    settings.gpt_image2_generate_path,
                    {
                        "model": model,
                        "prompt": prompt,
                        "n": 1,
                        "quality": "low",
                        "moderation": "low",
                        "size": size,
                        "output_format": "png",
                    },
                    generation_id=generation_id,
                ),
            )
            if result is None:
                return
            outputs, usage = result
            await _mark_generation_succeeded(generation_id, outputs, usage)
            return
        else:
            data = await _post_json(
                "/images/generations",
                {
                    "model": model,
                    "prompt": prompt,
                    "n": n,
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "response_format": "b64_json",
                },
            )
            if not await _generation_exists(generation_id):
                return
            outputs = await _save_image_outputs(user_id, generation_id, data)
        await _mark_generation_succeeded(generation_id, outputs, data.get("usage") if isinstance(data.get("usage"), dict) else None)
    except HTTPException as exc:
        await _mark_generation_failed(generation_id, exc.detail)
    except Exception as exc:
        await _mark_generation_failed(generation_id, exc)


async def _run_image_edit_job(
    generation_id: str,
    user_id: str,
    model: str,
    prompt: str,
    n: int,
    aspect_ratio: str,
    resolution: str,
    size: str,
    input_paths: list[str],
    image_urls: list[str],
) -> None:
    if not await _mark_generation_running(generation_id):
        return
    try:
        paths = [Path(path) for path in input_paths]
        if _is_gpt_image_model(model):
            files = await _image_edit_files_from_inputs(paths, image_urls)
            result = await _run_gpt_image_requests(
                generation_id,
                user_id,
                n,
                lambda: _post_gpt_multipart(
                    settings.gpt_image2_edit_path,
                    fields=[
                        ("model", model),
                        ("prompt", prompt),
                        ("n", "1"),
                        ("quality", "low"),
                        ("size", size),
                        ("output_format", "png"),
                        ("moderation", "low"),
                    ],
                    files=files,
                    generation_id=generation_id,
                ),
            )
            if result is None:
                return
            outputs, usage = result
            await _mark_generation_succeeded(generation_id, outputs, usage)
            return
        else:
            references = await _image_edit_references_from_inputs(paths, image_urls)
            payload: dict[str, object] = {
                "model": model,
                "prompt": prompt,
                "n": n,
                "resolution": resolution,
                "response_format": "b64_json",
            }
            if aspect_ratio != "auto":
                payload["aspect_ratio"] = aspect_ratio
            if len(references) == 1:
                payload["image"] = references[0]
            else:
                payload["images"] = references
            data = await _post_json("/images/edits", payload)
            if not await _generation_exists(generation_id):
                return
            outputs = await _save_image_outputs(user_id, generation_id, data)
        await _mark_generation_succeeded(generation_id, outputs, data.get("usage") if isinstance(data.get("usage"), dict) else None)
    except HTTPException as exc:
        await _mark_generation_failed(generation_id, exc.detail)
    except Exception as exc:
        await _mark_generation_failed(generation_id, exc)


async def _run_video_start_job(
    generation_id: str,
    mode: str,
    model: str,
    prompt: str,
    seconds: int,
    duration: int | None,
    aspect_ratio: str,
    resolution: str,
    image_urls: list[str],
    video_url: str | None,
) -> None:
    if not await _mark_generation_running(generation_id):
        return
    try:
        path, payload = _video_request_payload(
            mode,
            model,
            prompt,
            seconds,
            duration,
            aspect_ratio,
            resolution,
            image_urls,
            video_url,
        )
        data = await _post_json(path, payload, headers={"x-idempotency-key": f"rayue-{generation_id}-{uuid.uuid4().hex[:8]}"})
        request_id = data.get("id") or data.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise HTTPException(status_code=502, detail="视频接口没有返回任务 ID")
        await _mark_video_started(generation_id, request_id, data)
    except HTTPException as exc:
        await _mark_generation_failed(generation_id, exc.detail)
    except Exception as exc:
        await _mark_generation_failed(generation_id, exc)


async def _run_talking_photo_start_job(
    generation_id: str,
    user_id: str,
    prompt: str,
    image_path: str,
    audio_path: str,
) -> None:
    if not await _mark_generation_running(generation_id):
        return
    try:
        image_url = await _upload_talking_photo_file(user_id, Path(image_path), "image")
        audio_url = await _upload_talking_photo_file(user_id, Path(audio_path), "audio")
        payload = _talking_photo_task_payload(
            generation_id=generation_id,
            image_url=image_url,
            audio_url=audio_url,
            prompt=prompt,
        )
        data = await _post_talking_photo_task_json("/api/v1/task/publish", payload)
        request_id = _talking_photo_task_id(data)
        if not request_id:
            raise HTTPException(status_code=502, detail="Talking Photo 服务没有返回任务 ID")
        await _mark_talking_photo_started(generation_id, request_id, data)
    except HTTPException as exc:
        await _mark_generation_failed(generation_id, exc.detail)
    except Exception as exc:
        await _mark_generation_failed(generation_id, exc)


def _video_request_payload(
    mode: str,
    model: str,
    prompt: str,
    seconds: int,
    duration: int | None,
    aspect_ratio: str,
    resolution: str,
    image_urls: list[str],
    video_url: str | None,
) -> tuple[str, dict[str, object]]:
    if mode == "video-extension":
        if not video_url:
            raise HTTPException(status_code=400, detail="视频扩展需要输入视频")
        return (
            "/videos/extensions",
            {
                "model": model,
                "prompt": prompt,
                "duration": int(duration or seconds),
                "video": {"url": video_url},
            },
        )
    if mode == "video-edit":
        if not video_url:
            raise HTTPException(status_code=400, detail="视频编辑需要输入视频")
        return (
            "/videos/edits",
            {
                "model": model,
                "prompt": prompt,
                "video": {"url": video_url},
            },
        )

    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "seconds": str(seconds),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    if mode == "image-to-video":
        if not image_urls:
            raise HTTPException(status_code=400, detail="图生视频需要输入图片")
        payload["input_reference"] = {"image_url": image_urls[0]}
    elif mode == "reference-to-video":
        if not image_urls:
            raise HTTPException(status_code=400, detail="多参考图生视频需要输入图片")
        payload["reference_images"] = [{"url": url} for url in image_urls]
    return "/videos", payload


async def _run_gpt_image_requests(
    generation_id: str,
    user_id: str,
    count: int,
    request_factory,
) -> tuple[list[dict], dict | None] | None:
    if not await _generation_exists(generation_id):
        return None
    results = await asyncio.gather(*(request_factory() for _ in range(count)), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            raise result
    if not await _generation_exists(generation_id):
        return None

    outputs: list[dict] = []
    usages: list[dict] = []
    for data in results:
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail="GPT 图片接口返回格式异常")
        outputs.extend(
            await _save_image_outputs(
                user_id,
                generation_id,
                data,
                default_media_type="image/png",
                start_index=len(outputs),
            )
        )
        usage = data.get("usage")
        if isinstance(usage, dict):
            usages.append(usage)
    return outputs, _combined_usage(usages)


def _combined_usage(usages: list[dict]) -> dict | None:
    if not usages:
        return None
    if len(usages) == 1:
        return usages[0]
    return {"requests": usages}


async def _mark_generation_running(generation_id: str) -> bool:
    async with SessionLocal() as session:
        generation = await session.get(GrokGeneration, generation_id)
        if generation is None or generation.status in {"succeeded", "failed"}:
            return False
        generation.status = "running"
        generation.error = None
        generation.updated_at = utcnow()
        await session.commit()
        return True


async def _generation_exists(generation_id: str) -> bool:
    async with SessionLocal() as session:
        return await session.get(GrokGeneration, generation_id) is not None


async def _mark_generation_succeeded(generation_id: str, outputs: list[dict], usage: dict | None) -> None:
    async with SessionLocal() as session:
        generation = await session.get(GrokGeneration, generation_id)
        if generation is None:
            return
        generation.status = "succeeded"
        generation.outputs = outputs
        generation.usage = usage
        generation.error = None
        generation.completed_at = utcnow()
        generation.updated_at = utcnow()
        await session.commit()


async def _mark_generation_failed(generation_id: str, error: object) -> None:
    async with SessionLocal() as session:
        generation = await session.get(GrokGeneration, generation_id)
        if generation is None:
            return
        generation.status = "failed"
        generation.error = _friendly_grok_error(error)
        generation.updated_at = utcnow()
        generation.completed_at = utcnow()
        await session.commit()


async def _mark_video_started(generation_id: str, request_id: str, data: dict) -> None:
    async with SessionLocal() as session:
        generation = await session.get(GrokGeneration, generation_id)
        if generation is None:
            return
        generation.provider_request_id = request_id
        generation.status = _normalize_video_status(data.get("status"))
        generation.params = {**(generation.params or {}), "progress": _number_or_none(data.get("progress"))}
        if generation.status == "failed":
            generation.error = _friendly_grok_error(data.get("error") or "视频生成失败")
            generation.completed_at = utcnow()
        generation.updated_at = utcnow()
        await session.commit()


async def _mark_talking_photo_started(generation_id: str, request_id: str, data: dict) -> None:
    async with SessionLocal() as session:
        generation = await session.get(GrokGeneration, generation_id)
        if generation is None:
            return
        generation.provider_request_id = request_id
        generation.status = _normalize_talking_photo_status(data.get("task_status") or data.get("status") or 1)
        generation.params = {**(generation.params or {}), "progress": _number_or_none(data.get("progress"))}
        generation.updated_at = utcnow()
        await session.commit()


async def _refresh_talking_photo_generation(
    session: AsyncSession,
    user: User,
    generation: GrokGeneration,
) -> GrokGenerationOut:
    _require_talking_photo_config()
    if generation.status in {"succeeded", "failed"}:
        return _generation_out(generation)
    if not generation.provider_request_id and generation.status in {"queued", "running"}:
        return _generation_out(generation)
    if not generation.provider_request_id:
        generation.status = "failed"
        generation.error = "缺少 Talking Photo 任务 ID"
        generation.completed_at = utcnow()
        await session.commit()
        await session.refresh(generation)
        return _generation_out(generation)

    try:
        data = await _get_talking_photo_task_json("/api/v1/task/detail", {"task_id": generation.provider_request_id})
        generation.status = _normalize_talking_photo_status(data.get("task_status"))
        generation.params = {**(generation.params or {}), "progress": _number_or_none(data.get("progress"))}
        url = _talking_photo_video_url(data)
        if generation.status == "succeeded":
            if isinstance(url, str) and url and not generation.outputs:
                generation.outputs = [await _download_video_output(user.id, generation.id, url, {})]
                generation.completed_at = utcnow()
            elif not url and not generation.outputs:
                generation.status = "failed"
                generation.error = "Talking Photo 任务完成但没有返回视频 URL"
                generation.completed_at = utcnow()
        elif generation.status == "failed":
            generation.error = _friendly_grok_error(_talking_photo_error(data))
            generation.completed_at = utcnow()
    except HTTPException as exc:
        generation.error = _friendly_grok_error(exc.detail)
    except Exception as exc:
        generation.error = _friendly_grok_error(exc)

    generation.updated_at = utcnow()
    await session.commit()
    await session.refresh(generation)
    return _generation_out(generation)


async def _owned_generation(session: AsyncSession, user: User, generation_id: str) -> GrokGeneration:
    generation = await session.get(GrokGeneration, generation_id)
    if generation is None or generation.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    return generation


def _require_key() -> None:
    if not _grok_api_key():
        raise HTTPException(status_code=503, detail="GROK_API_KEY 未配置")


def _require_talking_photo_config() -> None:
    if not settings.talking_photo_task_secret_key:
        raise HTTPException(status_code=503, detail="Talking Photo 任务密钥未配置")
    if not settings.talking_photo_upload_secret_key:
        raise HTTPException(status_code=503, detail="Talking Photo 上传密钥未配置")


def _require_image_key(model: str) -> None:
    if _is_gpt_image_model(model):
        if not _gpt_api_key():
            logger.warning(
                "GPT image request rejected model=%s reason=api_key_missing base_url=%s",
                model,
                _upstream_base_url(settings.gpt_image2_base_url),
            )
            raise HTTPException(status_code=503, detail="GPT_IMAGE2_API_KEY 未配置")
        return
    _require_key()


def _is_gpt_image_model(model: str) -> bool:
    return model in GPT_IMAGE_MODELS


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    api_key = _grok_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="GROK_API_KEY 未配置")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _gpt_headers() -> dict[str, str]:
    api_key = _gpt_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="GPT_IMAGE2_API_KEY 未配置")
    return {"Authorization": f"Bearer {api_key}"}


async def _post_json(path: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    timeout = httpx.Timeout(settings.grok_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(
            f"{settings.grok_base_url.rstrip('/')}{path}",
            json=payload,
            headers=_headers(headers),
        )
    return _json_response(response)


async def _post_gpt_json(path: str, payload: dict, *, generation_id: str | None = None) -> dict:
    started_at = time.monotonic()
    timeout = httpx.Timeout(settings.gpt_image2_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                f"{settings.gpt_image2_base_url.rstrip('/')}{path}",
                json=payload,
                headers={**_gpt_headers(), "Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        _log_gpt_upstream_error(path, started_at, payload.get("model"), generation_id, exc)
        raise
    _log_gpt_upstream_response(path, started_at, payload.get("model"), generation_id, response)
    return _json_response(response)


async def _post_gpt_multipart(
    path: str,
    fields: list[tuple[str, str]],
    files: list[tuple[str, tuple[str, bytes, str]]],
    *,
    generation_id: str | None = None,
) -> dict:
    form_fields = {key: value for key, value in fields}
    started_at = time.monotonic()
    timeout = httpx.Timeout(settings.gpt_image2_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                f"{settings.gpt_image2_base_url.rstrip('/')}{path}",
                data=form_fields,
                files=files,
                headers=_gpt_headers(),
            )
    except httpx.HTTPError as exc:
        _log_gpt_upstream_error(path, started_at, form_fields.get("model"), generation_id, exc)
        raise
    _log_gpt_upstream_response(path, started_at, form_fields.get("model"), generation_id, response)
    return _json_response(response)


def _log_gpt_upstream_response(
    path: str,
    started_at: float,
    model: object,
    generation_id: str | None,
    response: httpx.Response,
) -> None:
    elapsed_ms = _elapsed_ms(started_at)
    if response.status_code >= 400:
        logger.warning(
            "GPT image upstream response failed generation_id=%s model=%s base_url=%s path=%s status_code=%s elapsed_ms=%s body=%s",
            generation_id,
            model,
            _upstream_base_url(settings.gpt_image2_base_url),
            path,
            response.status_code,
            elapsed_ms,
            _upstream_log_body(response.text),
        )
        return
    logger.info(
        "GPT image upstream response generation_id=%s model=%s base_url=%s path=%s status_code=%s elapsed_ms=%s",
        generation_id,
        model,
        _upstream_base_url(settings.gpt_image2_base_url),
        path,
        response.status_code,
        elapsed_ms,
    )


def _log_gpt_upstream_error(
    path: str,
    started_at: float,
    model: object,
    generation_id: str | None,
    error: httpx.HTTPError,
) -> None:
    logger.warning(
        "GPT image upstream request error generation_id=%s model=%s base_url=%s path=%s elapsed_ms=%s error_type=%s error=%s",
        generation_id,
        model,
        _upstream_base_url(settings.gpt_image2_base_url),
        path,
        _elapsed_ms(started_at),
        error.__class__.__name__,
        _upstream_log_body(error),
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _upstream_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"
    return _upstream_log_body(value, max_chars=200)


def _upstream_log_body(value: object, max_chars: int = 1000) -> str:
    text = " ".join(str(value or "").strip().split())
    for api_key in _sensitive_values():
        if api_key:
            text = text.replace(api_key, "[hidden]")
    if len(text) > max_chars:
        return f"{text[:max_chars]}..."
    return text


async def _get_json(path: str) -> dict:
    timeout = httpx.Timeout(settings.grok_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(f"{settings.grok_base_url.rstrip('/')}{path}", headers=_headers())
    return _json_response(response)


async def _post_talking_photo_task_json(path: str, payload: dict) -> dict:
    timeout = httpx.Timeout(settings.talking_photo_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(
            f"{settings.talking_photo_task_base_url.rstrip('/')}{path}",
            json=payload,
            headers=_talking_photo_task_headers(),
        )
    return _talking_photo_json_response(response)


async def _get_talking_photo_task_json(path: str, params: dict[str, str]) -> dict:
    timeout = httpx.Timeout(settings.talking_photo_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(
            f"{settings.talking_photo_task_base_url.rstrip('/')}{path}",
            params=params,
            headers=_talking_photo_task_headers(),
        )
    return _talking_photo_json_response(response)


async def _post_talking_photo_upload_json(path: str, payload: dict, user_id: str) -> dict:
    timeout = httpx.Timeout(settings.talking_photo_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(
            f"{settings.talking_photo_upload_url.rstrip('/')}{path}",
            json=payload,
            headers=_talking_photo_upload_headers(user_id),
        )
    return _talking_photo_json_response(response)


async def _put_talking_photo_upload(url: str, path: Path, media_type: str) -> None:
    timeout = httpx.Timeout(settings.talking_photo_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.put(url, content=path.read_bytes(), headers={"Content-Type": media_type})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=_short_error(response.text))


def _talking_photo_json_response(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=_short_error(response.text))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Talking Photo 服务返回了非 JSON 响应") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Talking Photo 服务返回格式异常")
    return data


def _json_response(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=_short_error(response.text))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Grok 返回了非 JSON 响应") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Grok 返回格式异常")
    return data


def _short_error(text: str) -> str:
    value = text.strip()
    for api_key in _sensitive_values():
        if api_key:
            value = value.replace(api_key, "[hidden]")
    for candidate in _error_payload_candidates(value):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        message = _provider_error_message(data)
        if message:
            return _friendly_grok_error(message)
    return _friendly_grok_error(value)


def _error_payload_candidates(value: str) -> list[str]:
    candidates = [value]
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            candidates.append(stripped.removeprefix("data:").strip())
    return [candidate for candidate in candidates if candidate and candidate != "[DONE]"]


def _provider_error_message(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    provider_error = data.get("error")
    if isinstance(provider_error, str):
        return provider_error
    if isinstance(provider_error, dict):
        message = provider_error.get("message")
        if isinstance(message, str):
            return message
        error_type = provider_error.get("type")
        if isinstance(error_type, str):
            return error_type
    message = data.get("message")
    if isinstance(message, str):
        return message
    return None


def _friendly_grok_error(value: object) -> str:
    text = str(value or "").strip()
    for api_key in _sensitive_values():
        if api_key:
            text = text.replace(api_key, "[hidden]")
    if "Generated image rejected by content moderation" in text:
        return "生成结果被 Grok 内容审核拒绝。请去掉真实人物姓名、性暗示、未成年人、暴力血腥或其他敏感描述后重试。"
    if "Generated video rejected by content moderation" in text:
        return "生成视频被 Grok 内容审核拒绝。请去掉真实人物姓名、性暗示、未成年人、暴力血腥或其他敏感描述后重试。"
    if "Upstream request failed" in text:
        return "GPT 图片上游请求失败。任务已结束，请稍后重试；如果连续出现，需要检查 GPT 图片服务的上游连接。"
    return text[:1000] or "Grok 请求失败"


def _sensitive_values() -> tuple[str | None, ...]:
    return (
        _grok_api_key(),
        _gpt_api_key(),
        settings.talking_photo_task_secret_key,
        settings.talking_photo_upload_secret_key,
    )


def _grok_api_key() -> str | None:
    return settings.grok_api_key


def _gpt_api_key() -> str | None:
    return settings.gpt_image2_api_key or settings.codex_api_key


def _talking_photo_task_payload(generation_id: str, image_url: str, audio_url: str, prompt: str) -> dict:
    return {
        "webhookOverride": "",
        "priority": 5,
        "algorithmFrom": "video_generation",
        "algorithmType": "talking_photo",
        "taskName": settings.talking_photo_task_name,
        "queueName": settings.talking_photo_queue,
        "data": {
            "video_id": f"rayue-{generation_id}-{uuid.uuid4().hex[:8]}",
            "talking_photo_url": image_url,
            "input_audio": audio_url,
            "input_video": "",
            "prompt": _talking_photo_prompt(prompt),
            "max_audio_duration": 60,
            "fps": 25,
            "parent_task_id": settings.talking_photo_parent_task_id,
        },
    }


def _talking_photo_prompt(value: str | None) -> str:
    prompt = (value or "").strip()
    return prompt or TALKING_PHOTO_DEFAULT_PROMPT


def _talking_photo_task_id(data: dict) -> str | None:
    value = data.get("task_id") or data.get("id")
    if isinstance(value, str) and value:
        return value
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    value = nested.get("task_id") or nested.get("id")
    return value if isinstance(value, str) and value else None


def _talking_photo_video_url(data: dict) -> str | None:
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    value = nested.get("video_url")
    if isinstance(value, str) and value:
        return value
    value = data.get("video_url")
    return value if isinstance(value, str) and value else None


def _talking_photo_error(data: dict) -> str:
    for key in ("error_msg", "error", "message", "msg"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Talking Photo 任务失败"


def _talking_photo_task_headers() -> dict[str, str]:
    secret = settings.talking_photo_task_secret_key
    if not secret:
        raise HTTPException(status_code=503, detail="Talking Photo 任务密钥未配置")
    now = int(time.time())
    token = _jwt_encode_hs256(
        {
            "uid": int(settings.talking_photo_uid),
            "id": settings.talking_photo_client_id,
            "type": settings.talking_photo_client_type,
            "email": "",
            "iat": now,
            "exp": now + 24 * 60 * 60,
            "team_id": settings.talking_photo_team_id,
        },
        secret,
    )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _talking_photo_upload_headers(user_id: str) -> dict[str, str]:
    secret = settings.talking_photo_upload_secret_key
    if not secret:
        raise HTTPException(status_code=503, detail="Talking Photo 上传密钥未配置")
    token = _jwt_encode_hs256(
        {
            "roles": ["app"],
            "userId": f"rayue-{user_id}",
            "issuer": "algorithm",
        },
        secret,
    )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _jwt_encode_hs256(payload: dict, secret: str) -> str:
    header_part = _base64url_json({"alg": "HS256", "typ": "JWT"})
    payload_part = _base64url_json(payload)
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_base64url(signature)}"


def _base64url_json(value: dict) -> str:
    data = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _base64url(data)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _image_generation_params(
    model: str,
    n: int,
    aspect_ratio: str,
    resolution: str,
    size: str,
) -> dict:
    if _is_gpt_image_model(model):
        return {
            "mode": "text-to-image",
            "n": n,
            "size": size,
            "quality": "low",
            "moderation": "low",
        }
    return {
        "mode": "text-to-image",
        "n": n,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }


def _image_edit_params(model: str, aspect_ratio: str, resolution: str, size: str) -> dict:
    if _is_gpt_image_model(model):
        return {
            "size": size,
            "quality": "low",
            "moderation": "low",
        }
    return {
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }


async def _save_image_outputs(
    user_id: str,
    generation_id: str,
    data: dict,
    default_media_type: str = "image/jpeg",
    start_index: int = 0,
) -> list[dict]:
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=502, detail="生图接口没有返回图片")
    outputs: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        content, media_type = await _image_bytes(item, default_media_type)
        suffix = _suffix_for_media_type(media_type, ".jpg")
        output_index = start_index + index
        filename = f"image-{output_index + 1}{suffix}"
        path = _output_path(user_id, generation_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        outputs.append(
            {
                "name": filename,
                "path": filename,
                "media_type": media_type,
                "size": path.stat().st_size,
                "url": f"/api/grok/generations/{generation_id}/download?index={output_index}",
            }
        )
    if not outputs:
        raise HTTPException(status_code=502, detail="生图接口没有返回可保存图片")
    return outputs


async def _image_bytes(item: dict, default_media_type: str) -> tuple[bytes, str]:
    b64_json = item.get("b64_json")
    if isinstance(b64_json, str) and b64_json:
        media_type = item.get("mime_type") if isinstance(item.get("mime_type"), str) else default_media_type
        return base64.b64decode(b64_json), media_type
    url = item.get("url")
    if isinstance(url, str) and url:
        timeout = httpx.Timeout(settings.grok_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="图片下载失败")
        media_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
        return response.content, media_type
    raise HTTPException(status_code=502, detail="图片响应缺少 b64_json 或 url")


async def _download_video_output(user_id: str, generation_id: str, url: str, video: dict) -> dict:
    timeout = httpx.Timeout(settings.grok_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="视频下载失败")
    media_type = response.headers.get("content-type", "video/mp4").split(";")[0]
    suffix = _suffix_for_media_type(media_type, ".mp4")
    filename = f"video{suffix}"
    path = _output_path(user_id, generation_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    duration = video.get("duration") if isinstance(video.get("duration"), (int, float)) else None
    return {
        "name": filename,
        "path": filename,
        "media_type": media_type,
        "size": path.stat().st_size,
        "url": f"/api/grok/generations/{generation_id}/download?index=0",
        "duration": duration,
    }


async def _video_media_inputs(
    session: AsyncSession,
    user: User,
    user_id: str,
    generation_id: str,
    manifest: list[dict],
    image_uploads: list[UploadFile],
    video_upload: UploadFile | None,
    *,
    image_urls: str | None,
    source_refs: str | None,
    video_url: str | None,
    video_source_ref: str | None,
) -> dict:
    image_reference_urls: list[str] = []
    input_video_url: str | None = None
    labels: list[dict[str, str]] = []
    image_index = 0
    video_index = 0

    if manifest:
        for item in manifest:
            kind = str(item.get("kind") or "").strip()
            source = str(item.get("source") or "").strip()
            alias = str(item.get("alias") or "").strip()
            if kind == "image":
                image_index += 1
                alias = alias or f"image{image_index}"
                image_reference_urls.append(
                    await _media_manifest_image_url(session, user, user_id, generation_id, item, image_uploads)
                )
                labels.append({"alias": alias, "kind": "image", "source": source})
            elif kind == "video":
                video_index += 1
                alias = alias or f"video{video_index}"
                if input_video_url is not None:
                    raise HTTPException(status_code=400, detail="视频编辑/扩展只能选择 1 个输入视频")
                input_video_url = await _media_manifest_video_url(session, user, user_id, generation_id, item, video_upload)
                labels.append({"alias": alias, "kind": "video", "source": source})
            else:
                raise HTTPException(status_code=400, detail="媒体引用类型无效")
        return {"image_urls": image_reference_urls, "video_url": input_video_url, "labels": labels}

    for upload in image_uploads:
        image_index += 1
        path = await _save_uploaded_image_input(user_id, generation_id, upload, image_index)
        image_reference_urls.append(_path_to_data_url(path))
        labels.append({"alias": f"image{image_index}", "kind": "image", "source": "upload"})
    for ref in _clean_source_refs(source_refs):
        image_index += 1
        image_reference_urls.append(await _source_image_data_url(session, user, ref))
        labels.append({"alias": f"image{image_index}", "kind": "image", "source": "generated"})
    for url in _clean_image_urls(image_urls):
        image_index += 1
        image_reference_urls.append(url)
        labels.append({"alias": f"image{image_index}", "kind": "image", "source": "url"})

    if video_upload is not None:
        input_video_url = _video_path_to_data_url(await _save_uploaded_video_input(user_id, generation_id, video_upload, 1))
        labels.append({"alias": "video1", "kind": "video", "source": "upload"})
    elif video_source_ref and video_source_ref.strip():
        input_video_url = await _source_video_data_url(session, user, video_source_ref.strip())
        labels.append({"alias": "video1", "kind": "video", "source": "generated"})
    elif video_url and video_url.strip():
        input_video_url = video_url.strip()
        labels.append({"alias": "video1", "kind": "video", "source": "url"})

    return {"image_urls": image_reference_urls, "video_url": input_video_url, "labels": labels}


async def _media_manifest_image_url(
    session: AsyncSession,
    user: User,
    user_id: str,
    generation_id: str,
    item: dict,
    uploads: list[UploadFile],
) -> str:
    source = str(item.get("source") or "").strip()
    if source == "upload":
        upload = _manifest_upload(uploads, item)
        path = await _save_uploaded_image_input(user_id, generation_id, upload, int(item.get("order") or 1))
        return _path_to_data_url(path)
    if source == "generated":
        ref = str(item.get("ref") or "").strip()
        if not ref:
            raise HTTPException(status_code=400, detail="历史图片引用无效")
        return await _source_image_data_url(session, user, ref)
    if source == "url":
        url = str(item.get("url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="图片 URL 为空")
        return url
    raise HTTPException(status_code=400, detail="图片来源无效")


async def _media_manifest_video_url(
    session: AsyncSession,
    user: User,
    user_id: str,
    generation_id: str,
    item: dict,
    upload: UploadFile | None,
) -> str:
    source = str(item.get("source") or "").strip()
    if source == "upload":
        if upload is None:
            raise HTTPException(status_code=400, detail="缺少上传视频")
        return _video_path_to_data_url(await _save_uploaded_video_input(user_id, generation_id, upload, 1))
    if source == "generated":
        ref = str(item.get("ref") or "").strip()
        if not ref:
            raise HTTPException(status_code=400, detail="历史视频引用无效")
        return await _source_video_data_url(session, user, ref)
    if source == "url":
        url = str(item.get("url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="视频 URL 为空")
        return url
    raise HTTPException(status_code=400, detail="视频来源无效")


def _manifest_upload(uploads: list[UploadFile], item: dict) -> UploadFile:
    try:
        index = int(item.get("index"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="上传图片引用无效") from exc
    if index < 0 or index >= len(uploads):
        raise HTTPException(status_code=400, detail="上传图片引用不存在")
    return uploads[index]


def _validate_video_media(mode: str, model: str, media_inputs: dict) -> None:
    image_count = len(media_inputs["image_urls"])
    has_video = bool(media_inputs["video_url"])
    if mode == "text-to-video":
        if image_count or has_video:
            raise HTTPException(status_code=400, detail="文生视频不需要输入图片或视频")
        return
    if mode == "image-to-video":
        if image_count != 1 or has_video:
            raise HTTPException(status_code=400, detail="单张首帧图生视频需要且只能选择 1 张图片")
        if model == "grok-imagine-video-1.5-preview" and image_count != 1:
            raise HTTPException(status_code=400, detail="1.5 preview 模型需要 1 张输入图片")
        return
    if mode == "reference-to-video":
        if has_video or image_count < 1 or image_count > MAX_VIDEO_REFERENCE_IMAGES:
            raise HTTPException(status_code=400, detail="多参考图生视频需要 1 到 7 张参考图片")
        return
    if mode in {"video-extension", "video-edit"}:
        if image_count or not has_video:
            raise HTTPException(status_code=400, detail="视频编辑/扩展需要且只能选择 1 个视频")


def _parse_media_manifest(value: str | None) -> list[dict]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="媒体引用清单格式无效") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="媒体引用清单必须是数组")
    if len(data) > MAX_VIDEO_REFERENCE_IMAGES + 1:
        raise HTTPException(status_code=400, detail="媒体引用数量过多")
    if not all(isinstance(item, dict) for item in data):
        raise HTTPException(status_code=400, detail="媒体引用清单格式无效")
    return data


async def _upload_to_data_url(upload: UploadFile) -> str:
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="输入图片为空")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="输入图片不能超过 50MB")
    media_type = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "image/jpeg"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片作为视频首帧")
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


async def _save_uploaded_image_inputs(user_id: str, generation_id: str, uploads: list[UploadFile]) -> list[Path]:
    return [await _save_uploaded_image_input(user_id, generation_id, upload, index) for index, upload in enumerate(uploads, start=1)]


async def _save_uploaded_image_input(user_id: str, generation_id: str, upload: UploadFile, index: int) -> Path:
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="输入图片为空")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="输入图片不能超过 50MB")
    media_type = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "image/jpeg"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片作为输入")
    input_dir = _input_dir(user_id, generation_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / _input_filename(upload.filename or f"image-{index}", media_type, index)
    path.write_bytes(data)
    return path


async def _save_uploaded_video_input(user_id: str, generation_id: str, upload: UploadFile, index: int) -> Path:
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="输入视频为空")
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="输入视频不能超过 200MB")
    media_type = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "video/mp4"
    suffix = Path(upload.filename or "").suffix.lower()
    if media_type != "video/mp4" and suffix != ".mp4":
        raise HTTPException(status_code=400, detail="视频编辑/扩展只支持 MP4")
    input_dir = _input_dir(user_id, generation_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / _input_filename(upload.filename or f"video-{index}.mp4", "video/mp4", index)
    path.write_bytes(data)
    return path


async def _save_talking_photo_input(user_id: str, generation_id: str, upload: UploadFile, kind: str, index: int) -> Path:
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="输入文件为空")
    if len(data) > settings.talking_photo_upload_max_bytes:
        raise HTTPException(status_code=413, detail="Talking Photo 输入文件不能超过 50MB")
    media_type = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or ""
    if kind == "image":
        _validate_talking_photo_image(media_type, upload.filename or "")
    elif kind == "audio":
        _validate_talking_photo_audio(media_type, upload.filename or "")
    else:
        raise HTTPException(status_code=400, detail="输入类型无效")
    input_dir = _input_dir(user_id, generation_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    fallback = "photo.jpg" if kind == "image" else "audio.mp3"
    path = input_dir / _input_filename(upload.filename or fallback, media_type or mimetypes.guess_type(fallback)[0] or "application/octet-stream", index)
    path.write_bytes(data)
    return path


async def _upload_talking_photo_file(user_id: str, path: Path, kind: str) -> str:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Talking Photo 输入文件不存在")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if kind == "image":
        _validate_talking_photo_image(media_type, path.name)
    elif kind == "audio":
        _validate_talking_photo_audio(media_type, path.name)
    else:
        raise HTTPException(status_code=400, detail="输入类型无效")
    file_size = path.stat().st_size
    if file_size > settings.talking_photo_upload_max_bytes:
        raise HTTPException(status_code=413, detail="Talking Photo 输入文件不能超过 50MB")
    signature = await _post_talking_photo_upload_json(
        "/api/v1/internal/upload/signature",
        {
            "biz_type": settings.talking_photo_biz_type,
            "file_name": path.name,
            "mime_type": media_type,
            "file_size": file_size,
        },
        user_id,
    )
    data = signature.get("data") if isinstance(signature.get("data"), dict) else {}
    upload_url = data.get("upload_url")
    final_url = data.get("final_url") or data.get("url")
    if not isinstance(upload_url, str) or not upload_url:
        raise HTTPException(status_code=502, detail="Talking Photo 上传服务没有返回上传 URL")
    if not isinstance(final_url, str) or not final_url:
        raise HTTPException(status_code=502, detail="Talking Photo 上传服务没有返回文件 URL")
    await _put_talking_photo_upload(upload_url, path, media_type)
    return final_url


def _validate_talking_photo_image(media_type: str, filename: str) -> None:
    suffix = Path(filename).suffix.lower()
    if media_type in TALKING_PHOTO_IMAGE_TYPES or suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return
    raise HTTPException(status_code=400, detail="Talking Photo 只支持 JPG、PNG 或 WebP 图片")


def _validate_talking_photo_audio(media_type: str, filename: str) -> None:
    suffix = Path(filename).suffix.lower()
    if media_type in TALKING_PHOTO_AUDIO_TYPES or suffix in {".mp3", ".wav", ".m4a", ".aac"}:
        return
    raise HTTPException(status_code=400, detail="Talking Photo 只支持 MP3、WAV、M4A 或 AAC 音频")


async def _save_source_image_inputs(
    session: AsyncSession,
    user: User,
    user_id: str,
    generation_id: str,
    refs: list[str],
) -> list[Path]:
    paths: list[Path] = []
    input_dir = _input_dir(user_id, generation_id)
    input_dir.mkdir(parents=True, exist_ok=True)
    for index, ref in enumerate(refs, start=1):
        name, data, media_type = await _source_image_data(session, user, ref)
        path = input_dir / _input_filename(name, media_type, index)
        path.write_bytes(data)
        paths.append(path)
    return paths


def _input_filename(filename: str, media_type: str, index: int) -> str:
    name = Path(filename).name
    stem = Path(name).stem or f"image-{index}"
    suffix = Path(name).suffix or _suffix_for_media_type(media_type, ".jpg")
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in stem)[:80] or f"image-{index}"
    return f"{index:02d}-{safe_stem}{suffix}"


async def _image_edit_references(
    uploads: list[UploadFile],
    image_urls: str | None,
) -> tuple[list[dict[str, str]], list[str]]:
    references: list[dict[str, str]] = []
    names: list[str] = []

    for upload in uploads:
        references.append({"url": await _upload_to_data_url(upload), "type": "image_url"})
        names.append(upload.filename or f"image-{len(names) + 1}")

    for url in _clean_image_urls(image_urls):
        references.append({"url": url, "type": "image_url"})
        names.append(url)

    return references, names


async def _image_edit_references_from_inputs(
    input_paths: list[Path],
    image_urls: list[str],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for path in input_paths:
        references.append({"url": _path_to_data_url(path), "type": "image_url"})
    for url in image_urls:
        references.append({"url": url, "type": "image_url"})
    return references


async def _image_edit_files(
    uploads: list[UploadFile],
    image_urls: str | None,
) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = []

    for upload in uploads:
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail="输入图片为空")
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="输入图片不能超过 50MB")
        media_type = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "image/jpeg"
        if not media_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="只支持图片作为图生图输入")
        files.append(("image[]", (Path(upload.filename or f"image-{len(files) + 1}.jpg").name, data, media_type)))

    for url in _clean_image_urls(image_urls):
        filename, data, media_type = await _download_image_file(url, len(files) + 1)
        files.append(("image[]", (filename, data, media_type)))

    return files


async def _image_edit_files_from_inputs(
    input_paths: list[Path],
    image_urls: list[str],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for path in input_paths:
        filename, data, media_type = _image_file_from_path(path)
        files.append(("image[]", (filename, data, media_type)))
    for url in image_urls:
        filename, data, media_type = await _download_image_file(url, len(files) + 1)
        files.append(("image[]", (filename, data, media_type)))
    return files


def _image_file_from_path(path: Path) -> tuple[str, bytes, str]:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="输入图片文件不存在")
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片作为输入")
    return path.name, path.read_bytes(), media_type


def _path_to_data_url(path: Path) -> str:
    _, data, media_type = _image_file_from_path(path)
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _video_path_to_data_url(path: Path) -> str:
    _, data, media_type = _video_file_from_path(path)
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _video_file_from_path(path: Path) -> tuple[str, bytes, str]:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="输入视频文件不存在")
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    if media_type != "video/mp4" and path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="视频编辑/扩展只支持 MP4")
    return path.name, path.read_bytes(), "video/mp4"


async def _download_image_file(url: str, index: int) -> tuple[str, bytes, str]:
    timeout = httpx.Timeout(settings.gpt_image2_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"参考图片下载失败：{url}")
    media_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"参考图片不是图片格式：{url}")
    suffix = _suffix_for_media_type(media_type, ".jpg")
    return f"reference-{index}{suffix}", response.content, media_type


def _input_image_names(uploads: list[UploadFile], image_urls: str | None) -> list[str]:
    names = [upload.filename or f"image-{index + 1}" for index, upload in enumerate(uploads)]
    names.extend(_clean_image_urls(image_urls))
    return names


async def _source_image_references(
    session: AsyncSession,
    user: User,
    refs: list[str],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for ref in refs:
        references.append({"url": await _source_image_data_url(session, user, ref), "type": "image_url"})
    return references


async def _source_image_files(
    session: AsyncSession,
    user: User,
    refs: list[str],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for ref in refs:
        name, data, media_type = await _source_image_data(session, user, ref)
        files.append(("image[]", (name, data, media_type)))
    return files


async def _source_image_names(session: AsyncSession, user: User, refs: list[str]) -> list[str]:
    names: list[str] = []
    for ref in refs:
        names.append(await _source_image_name(session, user, ref))
    return names


async def _source_image_name(session: AsyncSession, user: User, ref: str) -> str:
    name, _, _ = await _source_image_data(session, user, ref)
    return name


async def _source_image_data_url(session: AsyncSession, user: User, ref: str) -> str:
    _, data, media_type = await _source_image_data(session, user, ref)
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


async def _source_image_data(session: AsyncSession, user: User, ref: str) -> tuple[str, bytes, str]:
    generation_id, index = _parse_source_ref(ref)
    generation = await _owned_generation(session, user, generation_id)
    if generation.kind != "image":
        raise HTTPException(status_code=400, detail="只能选择已生成的图片作为输入")
    outputs = generation.outputs or []
    if index >= len(outputs):
        raise HTTPException(status_code=400, detail="选择的生成图片不存在")
    output = outputs[index]
    media_type = output.get("media_type") or "image/jpeg"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只能选择图片文件作为输入")
    path = _existing_output_path(user.id, generation.id, output.get("name", "output"))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="选择的生成图片文件不存在")
    return Path(output.get("name", path.name)).name, path.read_bytes(), media_type


async def _source_video_data_url(session: AsyncSession, user: User, ref: str) -> str:
    _, data, media_type = await _source_video_data(session, user, ref)
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


async def _source_video_data(session: AsyncSession, user: User, ref: str) -> tuple[str, bytes, str]:
    generation_id, index = _parse_source_ref(ref)
    generation = await _owned_generation(session, user, generation_id)
    if generation.kind != "video":
        raise HTTPException(status_code=400, detail="只能选择已生成的视频作为输入")
    outputs = generation.outputs or []
    if index >= len(outputs):
        raise HTTPException(status_code=400, detail="选择的生成视频不存在")
    output = outputs[index]
    media_type = output.get("media_type") or "video/mp4"
    if media_type != "video/mp4" and not media_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="只能选择视频文件作为输入")
    path = _existing_output_path(user.id, generation.id, output.get("name", "output"))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="选择的生成视频文件不存在")
    return Path(output.get("name", path.name)).name, path.read_bytes(), "video/mp4"


def _parse_source_ref(ref: str) -> tuple[str, int]:
    parts = ref.split(":", 1)
    generation_id = parts[0].strip()
    if not generation_id:
        raise HTTPException(status_code=400, detail="生成图片引用无效")
    if len(parts) == 1 or not parts[1].strip():
        return generation_id, 0
    try:
        index = int(parts[1])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="生成图片引用无效") from exc
    if index < 0:
        raise HTTPException(status_code=400, detail="生成图片引用无效")
    return generation_id, index


def _clean_source_refs(value: str | None) -> list[str]:
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def _clean_image_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def _input_dir(user_id: str, generation_id: str) -> Path:
    return _output_dir(user_id, generation_id) / ".inputs"


def _output_dir(user_id: str, generation_id: str) -> Path:
    return settings.creation_storage_dir / user_id / generation_id


def _legacy_output_dir(user_id: str, generation_id: str) -> Path:
    return settings.grok_storage_dir / user_id / generation_id


def _output_path(user_id: str, generation_id: str, filename: str) -> Path:
    safe_name = Path(filename).name
    return _output_dir(user_id, generation_id) / safe_name


def _existing_output_path(user_id: str, generation_id: str, filename: str) -> Path:
    safe_name = Path(filename).name
    path = _output_dir(user_id, generation_id) / safe_name
    if path.is_file():
        return path
    return _legacy_output_dir(user_id, generation_id) / safe_name


def _suffix_for_media_type(media_type: str, fallback: str) -> str:
    suffix = mimetypes.guess_extension(media_type.split(";")[0].strip())
    if suffix == ".jpe":
        return ".jpg"
    return suffix or fallback


def _normalize_video_status(value: object) -> str:
    if value in {"done", "completed", "succeeded", "success"}:
        return "succeeded"
    if value in {"failed", "error", "cancelled", "canceled"}:
        return "failed"
    if value in {"queued", "pending", "running", "processing", "in_progress"}:
        return "running"
    return "running"


def _normalize_talking_photo_status(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"finished", "done", "completed", "succeeded", "success", "3"}:
            return "succeeded"
        if normalized in {"error", "failed", "cancelled", "canceled", "user_input_error", "-3", "-2", "-1", "4", "6"}:
            return "failed"
        if normalized in {"queued", "pending", "running", "retrying", "processing", "in_progress", "0", "1", "2", "5"}:
            return "running"
    if value == 3:
        return "succeeded"
    if value in {-3, -2, -1, 4, 6}:
        return "failed"
    return "running"


def _is_talking_photo_generation(generation: GrokGeneration) -> bool:
    return generation.model == TALKING_PHOTO_MODEL or (generation.params or {}).get("mode") == "talking-photo"


def _number_or_none(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) else None


def _generation_out(generation: GrokGeneration) -> GrokGenerationOut:
    output = GrokGenerationOut.model_validate(generation)
    if output.error:
        output.error = _friendly_grok_error(output.error)
    return output
