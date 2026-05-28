#!/usr/bin/env python3
"""Call Gemini 3.5 Flash with local multimodal files via the native REST API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "gemini-3.5-flash"
BUNDLED_API_KEY = "AIzaSyCAafLa5s5VXB0xx1RgPYiQGg2Pr2MGV7I"
API_ROOT = "https://generativelanguage.googleapis.com"
MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".log": "text/plain",
    ".ts": "text/plain",
    ".tsx": "text/plain",
    ".jsx": "text/plain",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
}


def get_api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("GEMINI_API_KEY") or BUNDLED_API_KEY
    if not key:
        raise SystemExit("Missing Gemini API key.")
    return key


def guess_mime(path: Path) -> str:
    if path.suffix.lower() in MIME_OVERRIDES:
        return MIME_OVERRIDES[path.suffix.lower()]
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def normalize_file_response(data: dict[str, Any]) -> dict[str, Any]:
    file_obj = data.get("file")
    if isinstance(file_obj, dict):
        return file_obj
    return data


def request_json(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: Any | None = None,
    headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
    timeout: int = 120,
) -> tuple[dict[str, Any], dict[str, str]]:
    request_headers = dict(headers or {})
    request_headers["x-goog-api-key"] = api_key

    body = raw_body
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    req = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            data = json.loads(text) if text.strip() else {}
            return data, {k.lower(): v for k, v in response.headers.items()}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Gemini HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"Gemini request failed: {exc}") from exc


def upload_file(path: Path, *, api_key: str, timeout: int, poll_timeout: int) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    mime_type = guess_mime(path)
    size = path.stat().st_size
    start_headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    _, response_headers = request_json(
        f"{API_ROOT}/upload/v1beta/files",
        api_key=api_key,
        method="POST",
        headers=start_headers,
        payload={"file": {"display_name": path.name}},
        timeout=timeout,
    )
    upload_url = response_headers.get("x-goog-upload-url")
    if not upload_url:
        raise SystemExit("Gemini upload did not return x-goog-upload-url.")

    upload_headers = {
        "Content-Length": str(size),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }
    file_info, _ = request_json(
        upload_url,
        api_key=api_key,
        method="POST",
        headers=upload_headers,
        raw_body=path.read_bytes(),
        timeout=timeout,
    )
    file_obj = normalize_file_response(file_info)
    if not isinstance(file_obj, dict) or "uri" not in file_obj:
        raise SystemExit(f"Unexpected upload response: {json.dumps(file_info)[:1000]}")
    file_obj.setdefault("mimeType", mime_type)
    return wait_for_file(file_obj, api_key=api_key, timeout=timeout, poll_timeout=poll_timeout)


def wait_for_file(
    file_obj: dict[str, Any], *, api_key: str, timeout: int, poll_timeout: int
) -> dict[str, Any]:
    name = file_obj.get("name")
    if not name:
        return file_obj

    deadline = time.time() + poll_timeout
    while True:
        state = str(file_obj.get("state", "")).upper()
        if state in ("", "ACTIVE"):
            return file_obj
        if state == "FAILED":
            raise SystemExit(f"Gemini failed to process uploaded file: {name}")
        if time.time() >= deadline:
            raise SystemExit(f"Timed out waiting for Gemini file processing: {name}")

        time.sleep(2)
        fetched, _ = request_json(
            f"{API_ROOT}/v1beta/{name}",
            api_key=api_key,
            timeout=timeout,
        )
        file_obj = normalize_file_response(fetched)


def delete_file(file_obj: dict[str, Any], *, api_key: str, timeout: int) -> None:
    name = file_obj.get("name")
    if not name:
        return
    request_json(
        f"{API_ROOT}/v1beta/{name}",
        api_key=api_key,
        method="DELETE",
        timeout=timeout,
    )


def build_generate_payload(
    *,
    prompt: str,
    uploaded_files: list[dict[str, Any]],
    system: str | None,
    json_mode: bool,
    temperature: float | None,
    google_search: bool,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for file_obj in uploaded_files:
        mime_type = file_obj.get("mimeType") or file_obj.get("mime_type")
        parts.append(
            {
                "file_data": {
                    "mime_type": mime_type,
                    "file_uri": file_obj["uri"],
                }
            }
        )
    parts.append({"text": prompt})

    payload: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    if json_mode or temperature is not None:
        generation_config: dict[str, Any] = {}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        if temperature is not None:
            generation_config["temperature"] = temperature
        payload["generationConfig"] = generation_config
    if google_search:
        payload["tools"] = [{"google_search": {}}]
    return payload


def generate_content(
    *,
    model: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    url = f"{API_ROOT}/v1beta/models/{model}:generateContent"
    data, _ = request_json(url, api_key=api_key, method="POST", payload=payload, timeout=timeout)
    return data


def extract_text(response: dict[str, Any]) -> str:
    texts: list[str] = []
    for candidate in response.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze local video/audio/image/PDF files with Gemini 3.5 Flash."
    )
    parser.add_argument("--file", action="append", default=[], help="Local file path. Repeatable.")
    parser.add_argument("-p", "--prompt", help="Prompt for Gemini.")
    parser.add_argument("--system", help="Optional system instruction.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model. Default: {DEFAULT_MODEL}")
    parser.add_argument("--api-key", help="Override API key. GEMINI_API_KEY and bundled key are fallbacks.")
    parser.add_argument("--json", action="store_true", dest="json_mode", help="Ask Gemini for JSON output.")
    parser.add_argument("--google-search", action="store_true", help="Enable Google Search grounding.")
    parser.add_argument("--temperature", type=float, help="Optional generation temperature.")
    parser.add_argument("--raw", action="store_true", help="Print raw API JSON instead of extracted text.")
    parser.add_argument("--keep-files", action="store_true", help="Do not delete uploaded Files API objects.")
    parser.add_argument("--dry-run", action="store_true", help="Print request plan without calling Gemini.")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds.")
    parser.add_argument("--poll-timeout", type=int, default=300, help="File processing wait timeout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prompt = args.prompt
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        prompt = "Analyze the attached file(s) and summarize the important details."

    paths = [Path(p).expanduser().resolve() for p in args.file]
    if args.dry_run:
        plan = {
            "model": args.model,
            "files": [
                {"path": str(path), "mime_type": guess_mime(path), "exists": path.is_file()}
                for path in paths
            ],
            "prompt": prompt,
            "json_mode": args.json_mode,
            "google_search": args.google_search,
        }
        print(json.dumps(plan, indent=2))
        return 0

    api_key = get_api_key(args.api_key)
    uploaded_files: list[dict[str, Any]] = []
    try:
        for path in paths:
            uploaded_files.append(
                upload_file(
                    path,
                    api_key=api_key,
                    timeout=args.timeout,
                    poll_timeout=args.poll_timeout,
                )
            )
        payload = build_generate_payload(
            prompt=prompt,
            uploaded_files=uploaded_files,
            system=args.system,
            json_mode=args.json_mode,
            temperature=args.temperature,
            google_search=args.google_search,
        )
        response = generate_content(
            model=args.model,
            api_key=api_key,
            payload=payload,
            timeout=args.timeout,
        )
        if args.raw:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            text = extract_text(response)
            print(text if text else json.dumps(response, ensure_ascii=False, indent=2))
    finally:
        if not args.keep_files:
            for file_obj in uploaded_files:
                try:
                    delete_file(file_obj, api_key=api_key, timeout=args.timeout)
                except SystemExit as exc:
                    print(f"Warning: could not delete uploaded file: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
