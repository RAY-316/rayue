---
name: web-media-download
description: Download and prepare user-authorized online video or audio from public URLs using yt-dlp in the Rayue sandbox. Use when the user asks to download online media, save a video/audio URL, extract audio, fetch subtitles, thumbnails, metadata, or convert downloaded web media for later editing, analysis, transcription, or sharing. Do not use for DRM-protected, paid, private, or unauthorized content.
---

# Web Media Download

Use `yt-dlp` for public online video/audio URLs when the user has the right to access and use the media. The Rayue sandbox template includes `yt-dlp`, `ffmpeg`, `ffprobe`, Node, and yt-dlp support dependencies.

## Boundaries

- Do not help bypass DRM, paywalls, login-only access, region locks, or platform restrictions.
- Do not request or use cookies unless the user explicitly provides them for content they are allowed to access.
- For large playlists, ask before downloading everything. Prefer metadata inspection first.
- Save outputs in the current workspace so Rayue can show them as generated files.

## Quick Start

Inspect a URL before downloading:

```bash
python /home/user/.codex/skills/web-media-download/scripts/download_media.py --info "https://example.com/video"
```

Download a video as MP4:

```bash
python /home/user/.codex/skills/web-media-download/scripts/download_media.py "https://example.com/video" --output-dir downloads
```

Extract audio as MP3:

```bash
python /home/user/.codex/skills/web-media-download/scripts/download_media.py "https://example.com/video" --audio --audio-format mp3 --output-dir downloads
```

Download subtitles and thumbnail when available:

```bash
python /home/user/.codex/skills/web-media-download/scripts/download_media.py "https://example.com/video" --subs --thumbnail --output-dir downloads
```

## Workflow

1. If the request is broad or could download many files, run `--info` first and confirm scope.
2. Choose video, audio, subtitles, thumbnail, or metadata based on the user request.
3. Prefer MP4 for video and MP3/M4A for audio unless the user asks otherwise.
4. Keep filenames clear and let the helper print generated paths.
5. Verify outputs with `ls -lh`, `file`, or `ffprobe` for important media.
6. If downloading fails because a site changed or needs browser impersonation, retry once with `--impersonate chrome` through the helper. If it still fails, explain the site limitation without inventing a workaround.

## Notes

- `ffmpeg` is available for merging streams and post-processing.
- `yt-dlp --verbose` lists the exact dependency support available in the sandbox.
- Use ConvertX only after download when the user asks for a separate file conversion and ConvertX is a better fit.
