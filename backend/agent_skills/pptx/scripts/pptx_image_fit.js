const path = require("path");
const fs = require("fs");
const sharp = require("sharp");

async function imageMetadata(imagePath) {
  const meta = await sharp(imagePath).metadata();
  if (!meta.width || !meta.height) {
    throw new Error(`Could not read image dimensions: ${imagePath}`);
  }
  return { width: meta.width, height: meta.height };
}

async function addImageContain(slide, opts) {
  const { imagePath, x, y, w, h, ...imageOptions } = opts;
  const meta = await imageMetadata(imagePath);
  const imageRatio = meta.width / meta.height;
  const frameRatio = w / h;
  let drawW = w;
  let drawH = h;

  if (imageRatio > frameRatio) {
    drawH = w / imageRatio;
  } else {
    drawW = h * imageRatio;
  }

  slide.addImage({
    path: imagePath,
    x: x + (w - drawW) / 2,
    y: y + (h - drawH) / 2,
    w: drawW,
    h: drawH,
    ...imageOptions,
  });
}

async function addImageCover(slide, opts) {
  const { imagePath, x, y, w, h, outDir = ".pptx-image-cache", position = "centre", ...imageOptions } = opts;
  const targetRatio = w / h;
  const meta = await imageMetadata(imagePath);
  const baseWidth = Math.max(1200, meta.width);
  const targetWidth = baseWidth;
  const targetHeight = Math.max(1, Math.round(targetWidth / targetRatio));
  const parsed = path.parse(imagePath);
  const safeRatio = targetRatio.toFixed(4).replace(".", "p");
  const outputPath = path.join(outDir, `${parsed.name}-cover-${safeRatio}.png`);

  fs.mkdirSync(outDir, { recursive: true });
  await sharp(imagePath)
    .resize(targetWidth, targetHeight, { fit: "cover", position })
    .png()
    .toFile(outputPath);

  slide.addImage({
    path: outputPath,
    x,
    y,
    w,
    h,
    ...imageOptions,
  });
}

module.exports = {
  addImageContain,
  addImageCover,
};
