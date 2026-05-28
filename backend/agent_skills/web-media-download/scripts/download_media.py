#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path


DEFAULT_OUTPUT_TEMPLATE = "%(title).180B [%(id)s].%(ext)s"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download authorized public media with yt-dlp.")
    parser.add_argument("urls", nargs="*", help="Media URL(s)")
    parser.add_argument("--info", action="store_true", help="Print metadata JSON without downloading")
    parser.add_argument("--output-dir", default="downloads", help="Directory for downloaded files")
    parser.add_argument("--audio", action="store_true", help="Extract audio instead of downloading video")
    parser.add_argument("--audio-format", default="mp3", help="Audio format for --audio")
    parser.add_argument("--format", dest="format_selector", help="yt-dlp format selector")
    parser.add_argument("--merge-format", default="mp4", help="Container used when merging video/audio")
    parser.add_argument("--playlist", action="store_true", help="Allow playlist downloads")
    parser.add_argument("--subs", action="store_true", help="Download subtitles when available")
    parser.add_argument("--sub-langs", default="all", help="Subtitle languages, for example en,zh-Hans")
    parser.add_argument("--thumbnail", action="store_true", help="Download thumbnail when available")
    parser.add_argument("--metadata", action="store_true", help="Write info JSON beside downloaded media")
    parser.add_argument("--impersonate", help="Optional yt-dlp impersonation target, for example chrome")
    parser.add_argument("--max-filesize", help="yt-dlp max filesize, for example 500M")
    parser.add_argument("--extra", action="append", default=[], help="Extra yt-dlp argument; repeat as needed")
    args = parser.parse_args()

    if not args.urls:
        parser.error("at least one URL is required")

    if args.info:
        return run_info(args.urls, args.impersonate, args.extra)
    return run_download(args)


def run_info(urls: list[str], impersonate: str | None, extra_args: list[str]) -> int:
    cmd = ["yt-dlp", "--dump-single-json", "--no-warnings", "--no-playlist"]
    if impersonate:
        cmd.extend(["--impersonate", impersonate])
    cmd.extend(expand_extra_args(extra_args))
    cmd.extend(urls)
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or "yt-dlp metadata request failed")
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        summary = {
            "id": data.get("id"),
            "title": data.get("title"),
            "duration": data.get("duration"),
            "uploader": data.get("uploader"),
            "webpage_url": data.get("webpage_url"),
            "extractor": data.get("extractor"),
            "is_live": data.get("is_live"),
            "playlist_count": data.get("playlist_count"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_download(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--newline",
        "--trim-filenames",
        "180",
        "--paths",
        str(output_dir),
        "-o",
        DEFAULT_OUTPUT_TEMPLATE,
    ]
    if not args.playlist:
        cmd.append("--no-playlist")
    if args.audio:
        cmd.extend(["-x", "--audio-format", args.audio_format])
    else:
        cmd.extend(["--merge-output-format", args.merge_format])
        cmd.extend(["-f", args.format_selector or "bv*+ba/b"])
    if args.subs:
        cmd.extend(["--write-subs", "--write-auto-subs", "--sub-langs", args.sub_langs])
    if args.thumbnail:
        cmd.append("--write-thumbnail")
    if args.metadata:
        cmd.append("--write-info-json")
    if args.impersonate:
        cmd.extend(["--impersonate", args.impersonate])
    if args.max_filesize:
        cmd.extend(["--max-filesize", args.max_filesize])
    cmd.extend(expand_extra_args(args.extra))
    cmd.extend(args.urls)

    print("+", shlex.join(cmd))
    before = snapshot(output_dir)
    completed = subprocess.run(cmd, check=False, text=True)
    if completed.returncode != 0:
        raise SystemExit(f"yt-dlp failed with exit code {completed.returncode}")

    generated = [path for path in output_dir.rglob("*") if path.is_file() and path not in before]
    if not generated:
        generated = [path for path in output_dir.rglob("*") if path.is_file()]
    print("Generated files:")
    for path in sorted(generated):
        print(f"- {path} ({path.stat().st_size} bytes)")
    return 0


def snapshot(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {item for item in path.rglob("*") if item.is_file()}


def expand_extra_args(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        expanded.extend(shlex.split(value))
    return expanded


if __name__ == "__main__":
    raise SystemExit(main())
