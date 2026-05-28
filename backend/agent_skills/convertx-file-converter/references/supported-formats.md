# ConvertX Supported Formats

ConvertX supports many formats by delegating to specialized converters. Exact support depends on the source extension and target extension. Use:

```bash
python /home/user/.codex/skills/convertx-file-converter/scripts/convert_file.py --list-targets <source-extension>
```

## Converter Coverage

| Converter | Best for | Input count | Output count | Common examples |
| --- | --- | ---: | ---: | --- |
| LibreOffice | Office documents | 41 | 22 | doc, docx, odt, rtf, html, txt, csv, pdf -> docx, odt, html, pdf, rtf, txt |
| Pandoc | Markup, docs, slides | 43 | 65 | md, html, docx, ipynb, latex, rst, epub -> docx, html, pdf, pptx, revealjs, markdown |
| Calibre | E-books | 26 | 19 | epub, mobi, azw3, fb2, lit, lrf, pdf, txt -> epub, mobi, azw3, pdf, txt |
| FFmpeg | Audio and video | about 472 | about 199 | mp4, mov, webm, avi, mkv, mp3, wav, flac, m4a -> mp4, webm, gif, mp3, wav, flac |
| ImageMagick | Broad image conversion | 245 | 183 | jpg, png, gif, webp, heic, pdf, psd, tiff, bmp, ico -> png, jpg, webp, gif, pdf, tiff |
| GraphicsMagick | Images | 167 | 130 | jpg, png, gif, tiff, bmp, pdf, webp -> jpg, png, gif, tiff, bmp |
| Vips | Fast image conversion | 45 | 23 | jpg, png, webp, tiff, heif, avif, pdf, svg -> jpg, png, webp, tiff |
| Inkscape | Vector images | 7 | 17 | svg, eps, emf, wmf, pdf -> svg, pdf, png, eps, ps |
| resvg | SVG rendering | 1 | 1 | svg -> png |
| Potrace | Raster to vector | 4 | 11 | bmp, pgm, ppm, pnm -> svg, eps, pdf, dxf |
| VTracer | Raster to vector | 8 | 1 | png, jpg, bmp, gif, tiff, webp -> svg |
| libheif | HEIF/AVIF images | 2 | 4 | heic, heif -> jpg, png, y4m, heif |
| libjxl | JPEG XL | 11 | 11 | jxl, jpg, png, ppm, pfm, exr -> jxl, jpg, png, ppm, pfm, exr |
| Assimp | 3D assets | 77 | 23 | obj, fbx, gltf, glb, dae, stl, ply, 3ds -> obj, gltf, glb, dae, stl, ply |
| XeLaTeX | LaTeX documents | 1 | 1 | tex -> pdf |
| dvisvgm | TeX/DVI vector output | 4 | 2 | dvi, eps, pdf, ps -> svg, pdf |
| msgconvert | Outlook messages | 1 | 1 | msg -> eml; current container startup log reports this binary missing, so verify before relying on it |
| VCF | Contacts | 1 | 1 | vcf -> csv |
| Markitdown | Document text extraction | 6 | 1 | pdf, pptx, docx, xlsx, xls, html -> md |
| Dasel | Structured data | 5 | 4 | json, yaml, toml, csv, xml -> json, yaml, toml, xml |

## Practical Defaults

- **Best ConvertX fits over worker-local conversion**:
  - Video/audio transcoding: mp4/mov/webm/avi/mkv and mp3/wav/flac/m4a/ogg via FFmpeg.
  - Image/vector/HEIC/PSD conversion: jpg/png/webp/gif/tiff/heic/heif/avif/psd/bmp/ico/svg/eps/pdf via ImageMagick, GraphicsMagick, Vips, Inkscape, resvg, Potrace, VTracer, libheif, or libjxl.
  - E-books: epub/mobi/fb2 and document-to-ebook outputs via Calibre.
  - 3D assets: obj/fbx/glb/gltf/stl/ply/dae via Assimp.
  - Documents/markup when targets are exposed: doc/docx/odt/rtf/html/md/txt/csv/json/xml/yaml/pdf via LibreOffice, Pandoc, Calibre, Dasel, or Markitdown.
  - VCF contacts to CSV.
- **Prefer worker-local tools** when editing content, preserving custom layout with a native file skill, or using a target that ConvertX does not expose.
- Current local target checks show `ppt`, `pptx`, `odp`, `xls`, and `xlsx` have limited or no ConvertX targets. `pptx` currently exposes `md` only, so use local LibreOffice/soffice for `pptx -> pdf` when available.
- Markdown/HTML/docx to slides or rich text: ConvertX/Pandoc is often useful when listed by `--list-targets`.
- Spreadsheet to CSV: verify first. If `xlsx` has no ConvertX targets, use worker-local LibreOffice or Python.

## Limits

- Conversion quality varies by source file complexity and the selected converter.
- Password-protected or corrupted files may fail.
- Some conversions need an intermediate format; for example, convert a presentation to PDF before rendering pages to images.
- Treat `--list-targets <source-extension>` as the live source of truth; converter packages may exist in the container even when ConvertX does not expose a target for a specific source extension.
