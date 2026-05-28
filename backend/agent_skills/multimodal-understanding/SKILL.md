---
name: multimodal-understanding
description: "Use Gemini 3.5 Flash for multimodal analysis of local video, audio, images, PDFs, screenshots, and mixed media inputs. Trigger when a user uploads/provides/references media files, especially when they ask to understand, summarize, transcribe, inspect, or extract evidence from a video or audio clip. Also use for visual/PDF extraction, multimodal comparison, Gemini/Google AI requests, or Gemini through an OpenAI-compatible API. Includes a bundled Gemini API key fallback and a native Files API script."
---

# Multimodal Understanding

## Default Choice

Use `gemini-3.5-flash` when the task depends on video, audio, image, PDF, or mixed media input. Prefer this skill for uploaded videos that need understanding, timestamped summaries, visual/audio evidence extraction, or action/UI/event analysis. Its main advantages for this skill are broad multimodal input, long context, lower routine-task cost than flagship frontier models, Google Search grounding, and OpenAI-compatible chat integration.

Use the native helper first for media files:

```bash
python3 /home/user/.codex/skills/multimodal-understanding/scripts/gemini_multimodal.py \
  --file /absolute/path/to/video.mp4 \
  --prompt "Summarize the video with timestamps and key visual/audio evidence."
```

The helper reads `GEMINI_API_KEY` first, then falls back to the bundled internal key. Do not print or restate the key in responses.

## Workflow

1. If the user only says "analyze this video/file" and the goal is unclear, ask one concise question about the desired output.
2. Resolve local file paths before calling the helper. Use absolute paths.
3. Put media parts before the text prompt for single-file visual or video tasks.
4. Use `--google-search` only when the answer needs current external facts or citations; this can add search-tool charges.
5. Return the result directly, and call out uncertain timestamps, audio, OCR, or visual details when the model is not definitive.

## Commands

Analyze one file:

```bash
python3 /home/user/.codex/skills/multimodal-understanding/scripts/gemini_multimodal.py \
  --file /absolute/path/to/input.mov \
  --prompt "Create a concise scene-by-scene summary with timestamps."
```

Analyze several files together:

```bash
python3 /home/user/.codex/skills/multimodal-understanding/scripts/gemini_multimodal.py \
  --file /absolute/path/to/screen.png \
  --file /absolute/path/to/demo.mp4 \
  --prompt "Compare the screenshot against the demo and list UI mismatches."
```

Request JSON:

```bash
python3 /home/user/.codex/skills/multimodal-understanding/scripts/gemini_multimodal.py \
  --file /absolute/path/to/clip.mp4 \
  --json \
  --prompt "Return {summary, timeline:[{timestamp,event}], risks}."
```

Check payload shape without calling Gemini:

```bash
python3 /home/user/.codex/skills/multimodal-understanding/scripts/gemini_multimodal.py \
  --file /absolute/path/to/file.pdf \
  --prompt "Extract the main points." \
  --dry-run
```

## References

- Read `references/gemini-api-notes.md` for native Gemini Files API behavior, file limits, model strengths, and prompt patterns.
- Read `references/openai-compat.md` when integrating Gemini through OpenAI SDKs or Chat Completions-compatible clients.
