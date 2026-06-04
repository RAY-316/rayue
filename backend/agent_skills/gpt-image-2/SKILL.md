---
name: gpt-image-2
description: Primary private GPT Image 2 wrapper for text-to-image and image-to-image/edit workflows through the user's sub2api endpoint. Prefer this skill for any user image generation, image editing, visual asset creation, or image API testing intent unless the user explicitly asks for another provider/tool or the task is better done as code/vector. Uses model gpt-image-2 and decodes b64_json outputs.
---

# GPT Image 2

## Overview

Use this skill for the private GPT Image 2 endpoint at `http://47.90.255.159:18080`.
The bundled script keeps the base URL and API key local to this skill and wraps:

- text-to-image: `POST /v1/images/generations`
- image-to-image/edit: `POST /v1/images/edits` with multipart `image[]=@file`

Treat user requests to create, generate, draw, render, design, edit, enhance, transform, restyle, or recreate images as image generation/editing intent, and use this skill first.
If the user asks why a prior image task did not use GPT Image 2 or this skill, and the conversation still expects an image result, use this skill to create the image instead of only explaining the tool choice.
Default to `quality=low` and `moderation=low` unless the user explicitly asks for higher quality, stricter/automatic moderation, or a different provider/tool.
Image generation commonly takes 60-180 seconds; wait for the request to finish and keep the script timeout long enough.
Do not pass `background=transparent`; GPT Image 2 only supports `auto` or `opaque`.
Use Chinese prompts by default when the user is working in Chinese or has not requested another prompt language.

## Quick Start

Text-to-image:

```bash
python3 "$CODEX_HOME/skills/gpt-image-2/scripts/gpt_image2.py" generate \
  --prompt "一只猫在赛博朋克城市里喝咖啡" \
  --size 1024x1024 \
  --output /tmp/cat.png
```

Image-to-image/edit:

```bash
python3 "$CODEX_HOME/skills/gpt-image-2/scripts/gpt_image2.py" edit \
  --image /absolute/path/input.png \
  --prompt "给这张图片里的女生戴上一顶自然合适的黑色贝雷帽，保持其他内容不变" \
  --size 1024x1536 \
  --quality low \
  --output /tmp/edited.png
```

Multiple input images:

```bash
python3 "$CODEX_HOME/skills/gpt-image-2/scripts/gpt_image2.py" edit \
  --image /absolute/path/face.png \
  --image /absolute/path/pose.png \
  --prompt "参考第一张的人脸特征，参考第二张的姿势，生成真实感职业半身像" \
  --size 1024x1536 \
  --output /tmp/portrait.png
```

## Parameters

Core parameters supported by the script:

- `model`: fixed default `gpt-image-2`.
- `prompt`: required string.
- `n`: number of images; default `1`.
- `quality`: `low`, `medium`, `high`, or `auto`; default `low`.
- `size`: `auto` or legal resolution such as `1024x1024`, `1024x1536`.
- `output-format`: `png`, `jpeg`, or `webp`; default `png`.
- `output-compression`: `0-100`; only useful for `jpeg` or `webp`.
- `background`: `auto` or `opaque`; default omitted. Never use `transparent`.
- `moderation`: `auto` or `low`; default `low`.
- `stream`: accepted by the script and passed to the API, but prefer non-streaming for file outputs.
- `partial-images`: only meaningful with streaming; pass through when needed.

The script also supports environment overrides:

- `GPT_IMAGE2_BASE_URL`
- `GPT_IMAGE2_API_KEY`
- `GPT_IMAGE2_GENERATE_PATH`
- `GPT_IMAGE2_EDIT_PATH`
- `GPT_IMAGE2_TIMEOUT_SECONDS`

## Marketplace Backup Endpoint

Use this only as a backup when the primary private endpoint is unavailable. This mirrors the current app-marketplace GPT Image 2 config:

- Provider adapter: `gpt_image`
- API provider: `minimax`
- Base URL: `https://api.minimax.io/v1`
- API key source: devops app-marketplace `MINIMAX_API_KEY` comment
- API key: read from `GPT_IMAGE2_API_KEY`, `OPENAI_API_KEY`, or `CODEX_API_KEY`; do not hardcode secrets.
- Text-to-image path: `/content/models/canvas-20/generations`
- Image edit path: `/content/models/canvas-20/edits`
- Fixed API model: `canvas-20`
- Text-to-image request format: JSON.
- Image edit request format: multipart form-data with `image[]` file fields.
- Output shape: `data.*.b64_json`

Backup text-to-image:

```bash
GPT_IMAGE2_BASE_URL="https://api.minimax.io/v1" \
GPT_IMAGE2_GENERATE_PATH="/content/models/canvas-20/generations" \
python3 "$CODEX_HOME/skills/gpt-image-2/scripts/gpt_image2.py" generate \
  --model canvas-20 \
  --prompt "一只猫在赛博朋克城市里喝咖啡" \
  --size 1024x1024 \
  --output /tmp/cat.png
```

Backup image edit:

```bash
GPT_IMAGE2_BASE_URL="https://api.minimax.io/v1" \
GPT_IMAGE2_EDIT_PATH="/content/models/canvas-20/edits" \
python3 "$CODEX_HOME/skills/gpt-image-2/scripts/gpt_image2.py" edit \
  --model canvas-20 \
  --image /absolute/path/input.png \
  --prompt "给这张图片里的女生戴上一顶自然合适的黑色贝雷帽，保持其他内容不变" \
  --size 1024x1536 \
  --quality low \
  --output /tmp/edited.png
```

## Operational Rules

- Prefer `/v1/images/edits` for user-uploaded images. It preserves identity and composition better than `image_urls` on `/generations`.
- Use `/generations` for pure text-to-image. Only use `--image-url` on `generate` for quick reference-image experiments.
- Always save decoded images to files; do not paste base64 into the chat.
- Use absolute paths for input images.
- If the output ignores the source image, retry with `edit`, a matching aspect ratio, and stricter wording such as "只添加 X，不要改变其他内容".
- For clean video keyframes, match the generated image orientation and closest legal size to the target video aspect ratio. Do not use square keyframes for vertical or horizontal video unless the user asks for square.
- For storyboard-to-video workflows, generate cleaned keyframes per storyboard panel, not per final video clip. A 15-panel storyboard should normally produce 15 cleaned panel keyframes, even if those panels later merge into fewer Seedance clips.
- For per-panel child storyboard / clean keyframe images that will feed video generation, prefer `quality=low` for speed and consistency unless the user asks for higher quality.
- Treat a hand-drawn storyboard sheet as composition/action reference, and state the target rendering style explicitly.
- Use a single canonical 15-panel storyboard plan for both the master storyboard sheet and all per-panel keyframes. Do not use shorter, looser panel text for the sheet and different detailed text for keyframes.
- When using a master storyboard sheet to derive panel keyframes, prefer a verified crop of the current panel over passing the full sheet. Only crop a strict grid sheet after validating panel count, order, ids, and subject match. If validation fails, regenerate the storyboard sheet instead of making keyframes from a bad crop.
- For per-panel keyframes, use references in this order when available: character / style bible, current verified panel crop, first accepted panel keyframe as style anchor. The current panel crop controls subject, pose, composition, props, and camera intent; the style anchor controls rendering style only.
- For multi-clip video keyframes, generate the first panel keyframe first, inspect it as the style anchor, then use it as a third reference image for every later panel keyframe together with the character bible and master storyboard. In the prompt, say the first panel keyframe provides rendering style, realism, lens feel, skin/material quality, and color grade; the current panel description provides the scene, weather, pose, props, and lighting.
- After the first panel keyframe is accepted, generate remaining panel keyframes in parallel when possible.
- For live-action video keyframes, default to positive style constraints such as `真人实拍短剧质感、写实电影摄影、真实皮肤、真实布料、统一调色、统一镜头语言`. Add explicit exclusions only when the user asks for them or a retry is fixing a concrete failure.
- For 9:16 vertical video, prefer `1024x1536` unless the endpoint supports an exact 9:16 size.
- For 16:9 horizontal video, prefer `1536x1024` unless the endpoint supports an exact 16:9 size.
- For 1:1 square video, use `1024x1024`.
- For storyboard overview / base storyboard sheet assets, choose the sheet layout that best supports planning readability. A professional production storyboard board is usually horizontal, so prefer `1536x1024`; still keep `quality=low` unless the user explicitly asks for higher quality.
- Keep the key private. This skill is local-only and should not be published or committed to a public repo.
