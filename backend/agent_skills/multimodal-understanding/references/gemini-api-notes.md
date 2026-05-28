# Gemini API Notes

## Use native Gemini for media

Prefer `scripts/gemini_multimodal.py` for local video, audio, image, and PDF files. It uploads files through Gemini Files API, waits for processing, calls `models/gemini-3.5-flash:generateContent`, and deletes uploaded files by default.

Use native API instead of OpenAI compatibility when:

- The input is a video or large media/PDF file.
- The task needs multiple media files in one prompt.
- The request may exceed normal inline payload limits.
- Gemini-native tools such as `google_search` are needed.

## Model strengths to mention

- `gemini-3.5-flash` is the default model for this skill.
- Strong fit: video understanding, audio transcription/analysis, image/PDF inspection, OCR-like extraction, mixed-media comparison, long-context summaries, and high-volume low-cost calls.
- Relative to GPT-5.5, prefer Gemini 3.5 Flash when cost and media breadth matter more than maximum frontier reasoning.
- Use Google Search grounding for current facts or web-cited answers.

## File behavior

- Gemini supports text, image, audio, video, PDF, and other file inputs.
- Use Files API when total request size is over 100 MB. PDF has a 50 MB request limit.
- Files API allows files up to 2 GB and stores files for up to 48 hours.
- The helper deletes uploaded file objects after generation unless `--keep-files` is passed.

## Prompt patterns

Video summary:

```text
Summarize the video with a timeline. Include timestamps, visible UI/actions, spoken audio if present, and uncertainty notes.
```

Bug reproduction video:

```text
Watch the recording and identify the exact user actions, observed failure, likely trigger, and the first timestamp where behavior diverges from expected behavior.
```

Audio transcription:

```text
Transcribe the audio, label speakers when possible, and list decisions/action items separately.
```

PDF or screenshot extraction:

```text
Extract the visible data faithfully. Preserve table structure, flag illegible text, and return concise Markdown.
```

Multimodal comparison:

```text
Compare these files. List concrete mismatches only, cite which file each observation comes from, and include timestamps or visual locations when available.
```
