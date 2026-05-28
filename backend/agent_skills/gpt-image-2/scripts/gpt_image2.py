#!/usr/bin/env python3
import argparse
import base64
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from urllib import request, error

DEFAULT_BASE_URL = os.environ.get("GPT_IMAGE2_BASE_URL", "http://47.90.255.159:18080")
DEFAULT_API_KEY = (
    os.environ.get("GPT_IMAGE2_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or os.environ.get("CODEX_API_KEY")
    or ""
)
DEFAULT_GENERATE_PATH = os.environ.get("GPT_IMAGE2_GENERATE_PATH", "/v1/images/generations")
DEFAULT_EDIT_PATH = os.environ.get("GPT_IMAGE2_EDIT_PATH", "/v1/images/edits")
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT = int(os.environ.get("GPT_IMAGE2_TIMEOUT_SECONDS", "620"))

QUALITIES = ("low", "medium", "high", "auto")
FORMATS = ("png", "jpeg", "webp")
BACKGROUNDS = ("auto", "opaque")
MODERATIONS = ("auto", "low")


def fail(message, code=2):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def validate_common(args):
    if args.background == "transparent":
        fail("gpt-image-2 does not support transparent background; use auto or opaque.")
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        fail("--output-compression must be between 0 and 100.")


def auth_headers(content_type):
    if not DEFAULT_API_KEY:
        fail("GPT_IMAGE2_API_KEY, OPENAI_API_KEY, or CODEX_API_KEY is required.")
    return {
        "Authorization": f"Bearer {DEFAULT_API_KEY}",
        "Content-Type": content_type,
    }


def post_json(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{DEFAULT_BASE_URL.rstrip('/')}{path}",
        data=data,
        headers=auth_headers("application/json"),
        method="POST",
    )
    return read_json(req)


def post_multipart(path, fields, files):
    boundary = f"----gpt-image-2-{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields:
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, file_path in files:
        path_obj = Path(file_path)
        if not path_obj.is_file():
            fail(f"input image not found: {file_path}")
        mime = mimetypes.guess_type(path_obj.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{path_obj.name}"\r\n'
            ).encode()
        )
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
        body.extend(path_obj.read_bytes())
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode())

    req = request.Request(
        f"{DEFAULT_BASE_URL.rstrip('/')}{path}",
        data=bytes(body),
        headers=auth_headers(f"multipart/form-data; boundary={boundary}"),
        method="POST",
    )
    return read_json(req)


def read_json(req):
    try:
        with request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            payload = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        fail(f"HTTP {exc.code}: {detail}", code=1)
    except error.URLError as exc:
        fail(f"request failed: {exc}", code=1)

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        fail(f"response is not JSON: {payload[:500]}", code=1)


def common_payload(args):
    payload = {
        "model": args.model,
        "prompt": args.prompt,
        "n": args.n,
        "quality": args.quality,
        "moderation": args.moderation,
    }
    if args.size:
        payload["size"] = args.size
    if args.output_format:
        payload["output_format"] = args.output_format
    if args.output_compression is not None:
        payload["output_compression"] = args.output_compression
    if args.background:
        payload["background"] = args.background
    if args.stream:
        payload["stream"] = True
    if args.partial_images is not None:
        payload["partial_images"] = args.partial_images
    return payload


def output_paths(args, count):
    if args.output and count != 1:
        fail("--output can only be used when n=1.")

    if args.output:
        return [Path(args.output)]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = args.output_format or "png"
    return [output_dir / f"{args.prefix}-{index + 1}.{suffix}" for index in range(count)]


def save_images(response, args):
    data = response.get("data")
    if not isinstance(data, list) or not data:
        fail(f"response does not contain data images: {json.dumps(response)[:500]}", code=1)

    paths = output_paths(args, len(data))

    for item, path_obj in zip(data, paths):
        b64 = item.get("b64_json")
        if not b64:
            fail(f"image item missing b64_json: {item}", code=1)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_bytes(base64.b64decode(b64))
        print(path_obj)

    revised = data[0].get("revised_prompt")
    if revised:
        print(f"revised_prompt: {revised}", file=sys.stderr)


def command_generate(args):
    validate_common(args)
    payload = common_payload(args)
    if args.image_url:
        payload["image_urls"] = args.image_url
    save_images(post_json(DEFAULT_GENERATE_PATH, payload), args)


def command_edit(args):
    validate_common(args)
    if not args.image:
        fail("edit requires at least one --image.")

    fields = [
        ("model", args.model),
        ("prompt", args.prompt),
        ("n", args.n),
        ("quality", args.quality),
        ("size", args.size),
        ("output_format", args.output_format),
        ("moderation", args.moderation),
        ("output_compression", args.output_compression),
        ("background", args.background),
        ("stream", "true" if args.stream else None),
        ("partial_images", args.partial_images),
    ]
    files = [("image[]", image_path) for image_path in args.image]
    save_images(post_multipart(DEFAULT_EDIT_PATH, fields, files), args)


def add_common_args(parser):
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--quality", choices=QUALITIES, default="low")
    parser.add_argument("--size", default="auto")
    parser.add_argument("--output-format", choices=FORMATS, default="png")
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--background", choices=BACKGROUNDS)
    parser.add_argument("--moderation", choices=MODERATIONS, default="low")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--partial-images", type=int)
    parser.add_argument("--output")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--prefix", default="image")


def main():
    parser = argparse.ArgumentParser(description="Private GPT Image 2 API wrapper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Text-to-image or quick image-url reference generation.")
    add_common_args(generate)
    generate.add_argument("--image-url", action="append", help="Optional public reference image URL.")
    generate.set_defaults(func=command_generate)

    edit = subparsers.add_parser("edit", help="Image-to-image/edit with multipart image[] file uploads.")
    add_common_args(edit)
    edit.add_argument("--image", action="append", required=True, help="Local input image path. Repeat for multiple images.")
    edit.set_defaults(func=command_edit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
