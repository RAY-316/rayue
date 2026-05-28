---
name: convertx-file-converter
description: Convert local files between document, image, audio, video, ebook, vector, 3D asset, markup, contact, and structured-data formats through Rayue's ConvertX service. Use when Codex needs a shared conversion service instead of installing/running heavy tools in the worker, especially for doc/docx/odt/html/markdown/pdf conversions, image/vector/HEIC/PSD conversions, video/audio transcoding, ebook conversion, 3D asset conversion, VCF to CSV, or preparing uploaded/reference files for analysis. Always check targets for uncertain source formats.
---

# ConvertX File Converter

Use Rayue's local ConvertX service instead of installing conversion tools inside the sandbox. The service is reached through the Rayue backend proxy configured by `RAYUE_CONVERTX_API_BASE_URL`.

## When ConvertX Is Better Than Worker-Local Conversion

Prefer this skill when the task is mainly format conversion and one of these applies:

- **Video/audio transcoding**: mp4/mov/webm/avi/mkv and mp3/wav/flac/m4a/ogg conversions. ConvertX has FFmpeg in the shared service, so the worker does not need codec packages.
- **Image and vector conversion**: jpg/png/webp/gif/tiff/heic/heif/avif/psd/bmp/ico/svg/eps/pdf to common image/vector outputs. ConvertX has ImageMagick, GraphicsMagick, Vips, Inkscape, resvg, Potrace, VTracer, libheif, and libjxl.
- **E-books**: epub/mobi/fb2 and related document-to-ebook conversions. ConvertX has Calibre, which is expensive to install in a fresh worker.
- **Document and markup export when supported**: doc/docx/odt/rtf/html/md/txt/csv/json/xml/yaml/pdf conversions through LibreOffice, Pandoc, Calibre, Dasel, or Markitdown.
- **3D assets**: obj/fbx/glb/gltf/stl/ply/dae conversion through Assimp.
- **Contacts**: vcf to csv.
- **Avoiding dependency setup**: use ConvertX when the worker would otherwise need apt/npm/pip installs just to convert a file.

Use worker-local tools instead when the task needs content editing, precise layout manipulation, custom scripts, or when ConvertX does not expose the requested target. Current local ConvertX target checks show limited/no conversion targets for `ppt`, `pptx`, `odp`, `xls`, and `xlsx`; for example `pptx` currently exposes `md` only, so `pptx -> pdf` is better handled by local LibreOffice/soffice when available.

## Quick Start

Convert one file:

```bash
python /home/user/.codex/skills/convertx-file-converter/scripts/convert_file.py input.docx --to pdf --output input.pdf
```

List available targets for a source extension:

```bash
python /home/user/.codex/skills/convertx-file-converter/scripts/convert_file.py --list-targets docx
```

Force a converter only when the automatic choice is wrong:

```bash
python /home/user/.codex/skills/convertx-file-converter/scripts/convert_file.py report.md --to pptx --converter pandoc
```

## Workflow

1. Decide whether this is a pure format conversion. If the file needs editing or generated content, use the native file skill first and ConvertX only for export.
2. Check whether the requested conversion is likely supported. For details, read `references/supported-formats.md`.
3. If unsure, run `--list-targets <extension>` before converting. Treat the live target list as authoritative.
4. Convert into the current workspace or a user-visible output path. Use clear filenames with the target extension.
5. Verify the output exists and is non-empty. For important document outputs, do a lightweight inspection such as `file`, `ls -lh`, text extraction, or a PDF/PPTX render check when available.
6. If ConvertX reports no converter, try a reasonable intermediate format or use a worker-local tool that is already available. Examples: `docx -> pdf -> png`, `svg -> png`, `mp4 -> mov`, `epub -> pdf`.

## Notes

- Do not call ConvertX directly on `127.0.0.1`; inside the sandbox that address is the sandbox, not Rayue's server.
- The script needs `RAYUE_CONVERTX_API_BASE_URL`. If it is missing, report the configuration problem instead of inventing an endpoint.
- ConvertX is best for format conversion. For content editing, use the native document/spreadsheet/slides/PDF skills first, then ConvertX only for export if useful.
- Large video, 3D, or office conversions can take minutes.
