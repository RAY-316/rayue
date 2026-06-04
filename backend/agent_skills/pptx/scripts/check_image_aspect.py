"""Detect likely stretched images in a PPTX file.

The check compares each picture frame's aspect ratio with the embedded image's
effective aspect ratio. If PowerPoint crop metadata (<a:srcRect>) is present,
the checker accounts for the visible crop instead of blindly accepting it.
"""

from __future__ import annotations

import argparse
import re
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image


EMU_PER_INCH = 914400
REL_RE = re.compile(r'Id="([^"]+)"[^>]*Target="([^"]+)"')
SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
PIC_RE = re.compile(r"<p:pic>.*?</p:pic>", re.S)


def _slide_sort_key(name: str) -> int:
    match = SLIDE_RE.match(name)
    return int(match.group(1)) if match else 0


def _relationships(zip_file: ZipFile, slide_name: str) -> dict[str, str]:
    rel_name = slide_name.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"
    if rel_name not in zip_file.namelist():
        return {}
    rel_xml = zip_file.read(rel_name).decode("utf-8", errors="ignore")
    return dict(REL_RE.findall(rel_xml))


def _media_path(target: str) -> str:
    return "ppt/" + target.replace("../", "")


def _src_rect(pic_xml: str) -> tuple[int, int, int, int]:
    match = re.search(r"<a:srcRect([^>]*)/>", pic_xml)
    if not match:
        return (0, 0, 0, 0)
    attrs = match.group(1)
    values = {}
    for key in ("l", "r", "t", "b"):
        attr = re.search(rf'{key}="(\d+)"', attrs)
        values[key] = int(attr.group(1)) if attr else 0
    return values["l"], values["r"], values["t"], values["b"]


def _effective_ratio(width: int, height: int, src_rect: tuple[int, int, int, int]) -> float:
    left, right, top, bottom = src_rect
    visible_width = width * max(1, 100000 - left - right) / 100000
    visible_height = height * max(1, 100000 - top - bottom) / 100000
    return visible_width / visible_height


def check_pptx(path: Path, tolerance: float, min_inches: float) -> list[str]:
    issues: list[str] = []
    with ZipFile(path) as zip_file:
        slides = sorted(
            [name for name in zip_file.namelist() if SLIDE_RE.match(name)],
            key=_slide_sort_key,
        )
        names = set(zip_file.namelist())
        image_cache: dict[str, tuple[int, int]] = {}

        for slide_name in slides:
            rels = _relationships(zip_file, slide_name)
            xml = zip_file.read(slide_name).decode("utf-8", errors="ignore")
            slide_num = _slide_sort_key(slide_name)

            for pic_index, match in enumerate(PIC_RE.finditer(xml), 1):
                pic = match.group(0)
                ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', pic)
                embed = re.search(r'r:embed="([^"]+)"', pic)
                if not ext or not embed:
                    continue
                cx, cy = map(int, ext.groups())
                if cx <= 0 or cy <= 0:
                    continue
                if min(cx, cy) / EMU_PER_INCH < min_inches:
                    continue
                target = rels.get(embed.group(1))
                if not target:
                    continue
                media = _media_path(target)
                if media not in names:
                    continue
                if media not in image_cache:
                    with Image.open(BytesIO(zip_file.read(media))) as image:
                        image_cache[media] = image.size
                width, height = image_cache[media]
                if width <= 0 or height <= 0:
                    continue
                frame_ratio = cx / cy
                image_ratio = _effective_ratio(width, height, _src_rect(pic))
                delta = abs(frame_ratio / image_ratio - 1)
                if delta > tolerance:
                    issues.append(
                        f"slide {slide_num} picture {pic_index}: frame ratio {frame_ratio:.3f} "
                        f"vs image ratio {image_ratio:.3f} ({media}); likely stretched"
                    )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.12)
    parser.add_argument("--min-inches", type=float, default=0.35)
    args = parser.parse_args()

    issues = check_pptx(args.pptx, args.tolerance, args.min_inches)
    if issues:
        print("Possible distorted images found:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("PASSED - no likely stretched images found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
