#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert files through Rayue ConvertX.")
    parser.add_argument("input", nargs="?", help="Input file path")
    parser.add_argument("--to", dest="target", help="Target extension, for example pdf")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--converter", help="Optional ConvertX converter name")
    parser.add_argument("--base-url", default=os.environ.get("RAYUE_CONVERTX_API_BASE_URL"))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--list-targets", metavar="EXT", help="List ConvertX targets for a source extension")
    args = parser.parse_args()

    base_url = (args.base_url or "").rstrip("/")
    if not base_url:
        raise SystemExit("RAYUE_CONVERTX_API_BASE_URL is not configured")

    if args.list_targets:
        list_targets(base_url, args.list_targets, args.timeout)
        return 0

    if not args.input or not args.target:
        parser.error("input and --to are required unless --list-targets is used")

    input_path = Path(args.input)
    if not input_path.is_file():
        raise SystemExit(f"Input file not found: {input_path}")

    target = clean_extension(args.target)
    output_path = Path(args.output) if args.output else input_path.with_suffix(f".{target}")
    converted = convert_file(
        base_url=base_url,
        input_path=input_path,
        target=target,
        output_path=output_path,
        converter=args.converter,
        timeout=args.timeout,
    )
    print(converted)
    return 0


def list_targets(base_url: str, extension: str, timeout: int) -> None:
    query = urlencode({"file_type": clean_extension(extension)})
    payload, _headers = request_bytes(f"{base_url}/targets?{query}", timeout=timeout)
    data = json.loads(payload.decode("utf-8"))
    targets = data.get("targets") or []
    if not targets:
        print("No targets found")
        return
    for item in targets:
        print(f'{item["target"]}\t{item["converter"]}')


def convert_file(
    *,
    base_url: str,
    input_path: Path,
    target: str,
    output_path: Path,
    converter: str | None,
    timeout: int,
) -> Path:
    fields = {"target_format": target}
    if converter:
        fields["converter"] = converter
    content_type = mimetypes.guess_type(input_path.name)[0] or "application/octet-stream"
    body, boundary = encode_multipart(
        fields=fields,
        file_field="file",
        filename=input_path.name,
        content_type=content_type,
        data=input_path.read_bytes(),
    )
    payload, _headers = request_bytes(
        f"{base_url}/convert",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=timeout,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    if output_path.stat().st_size == 0:
        raise SystemExit(f"Converted file is empty: {output_path}")
    return output_path


def request_bytes(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int,
) -> tuple[bytes, dict[str, str]]:
    request = Request(url, data=data, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), dict(response.headers.items())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("detail") or detail
        except json.JSONDecodeError:
            pass
        raise SystemExit(f"ConvertX request failed ({exc.code}): {detail}") from exc


def encode_multipart(
    *,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> tuple[bytes, str]:
    boundary = f"rayue-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def clean_extension(value: str) -> str:
    cleaned = value.strip().lower().lstrip(".")
    if not cleaned or "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        raise SystemExit(f"Invalid extension: {value}")
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
