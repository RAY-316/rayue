"use client";

import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  AudioLines,
  ArrowLeft,
  Check,
  Download,
  FileAudio,
  ImagePlus,
  Images,
  Loader2,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  Video,
  Wand2,
  X,
} from "lucide-react";
import clsx from "clsx";
import {
  GrokGeneration,
  createTalkingPhoto,
  createGrokVideo,
  deleteGrokGeneration,
  editGrokImage,
  generateGrokImage,
  getAuthToken,
  grokDownloadUrl,
  listGrokGenerations,
  me,
  refreshGrokGeneration,
} from "@/lib/api";

const IMAGE_MODELS = ["gpt-image-2", "grok-imagine-image-quality", "grok-imagine-image"];

const IMAGE_COUNTS = [1, 2, 3, 4];
const IMAGE_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "auto"];
const GPT_IMAGE_SIZES = ["1024x1024", "1536x1024", "1024x1536", "auto"];
const IMAGE_EDIT_MAX_REFERENCES = 3;
const VIDEO_SECONDS_OPTIONS = Array.from({ length: 15 }, (_, index) => index + 1);
const VIDEO_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"];
const VIDEO_RESOLUTIONS = [
  { value: "480p", label: "480p" },
  { value: "720p", label: "720p" },
];
const VIDEO_MODELS = ["grok-imagine-video", "grok-imagine-video-1.5-preview"];
const VIDEO_REFERENCE_MAX = 7;
const VIDEO_MODES: { value: VideoMode; label: string }[] = [
  { value: "image-to-video", label: "单图首帧" },
  { value: "reference-to-video", label: "多参考图" },
  { value: "text-to-video", label: "文生视频" },
  { value: "video-extension", label: "视频扩展" },
  { value: "video-edit", label: "视频编辑" },
];

const statusLabel: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "完成",
  failed: "失败",
};
const HISTORY_PAGE_SIZE = 20;

type GeneratedImageOption = {
  ref: string;
  src: string;
  label: string;
  model: string;
};

type VideoMode = "text-to-video" | "image-to-video" | "reference-to-video" | "video-extension" | "video-edit";
type MediaKind = "image" | "video";
type MediaSource = "upload" | "generated" | "url";

type VideoMediaItem = {
  id: string;
  kind: MediaKind;
  source: MediaSource;
  label: string;
  previewUrl: string;
  file?: File;
  ref?: string;
  url?: string;
};

type AliasedMediaItem = VideoMediaItem & {
  alias: string;
};

type GeneratedMediaOption = {
  ref: string;
  src: string;
  label: string;
  model: string;
  kind: MediaKind;
};

type PromptMention = {
  alias: string;
  kind: MediaKind;
  previewUrl: string;
};

export default function CreatePage() {
  const [authChecked, setAuthChecked] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [mode, setMode] = useState<"image" | "imageEdit" | "video" | "talkingPhoto">("image");
  const [history, setHistory] = useState<GrokGeneration[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [imagePrompt, setImagePrompt] = useState("");
  const [imageModel, setImageModel] = useState("grok-imagine-image-quality");
  const [imageRatio, setImageRatio] = useState("1:1");
  const [imageResolution, setImageResolution] = useState("1k");
  const [imageSize, setImageSize] = useState("auto");
  const [imageCount, setImageCount] = useState(1);

  const [editPrompt, setEditPrompt] = useState("");
  const [editModel, setEditModel] = useState("grok-imagine-image-quality");
  const [editRatio, setEditRatio] = useState("1:1");
  const [editResolution, setEditResolution] = useState("1k");
  const [editSize, setEditSize] = useState("auto");
  const [editCount, setEditCount] = useState(1);
  const [editImages, setEditImages] = useState<File[]>([]);
  const [editImageUrls, setEditImageUrls] = useState("");
  const [editSourceRefs, setEditSourceRefs] = useState<string[]>([]);
  const editFileInputRef = useRef<HTMLInputElement | null>(null);

  const [videoPrompt, setVideoPrompt] = useState("");
  const [videoMode, setVideoMode] = useState<VideoMode>("image-to-video");
  const [videoModel, setVideoModel] = useState("grok-imagine-video-1.5-preview");
  const [videoSeconds, setVideoSeconds] = useState(4);
  const [videoDuration, setVideoDuration] = useState(4);
  const [videoRatio, setVideoRatio] = useState("16:9");
  const [videoResolution, setVideoResolution] = useState("480p");
  const [videoMedia, setVideoMedia] = useState<VideoMediaItem[]>([]);
  const [videoImageUrlInput, setVideoImageUrlInput] = useState("");
  const [videoVideoUrlInput, setVideoVideoUrlInput] = useState("");
  const videoImageFileInputRef = useRef<HTMLInputElement | null>(null);
  const videoVideoFileInputRef = useRef<HTMLInputElement | null>(null);
  const videoMediaRef = useRef<VideoMediaItem[]>([]);

  const [talkingPhotoPrompt, setTalkingPhotoPrompt] = useState("");
  const [talkingPhotoImage, setTalkingPhotoImage] = useState<File | null>(null);
  const [talkingPhotoAudio, setTalkingPhotoAudio] = useState<File | null>(null);
  const talkingPhotoImageInputRef = useRef<HTMLInputElement | null>(null);
  const talkingPhotoAudioInputRef = useRef<HTMLInputElement | null>(null);

  const runningGenerations = useMemo(
    () => history.filter((item) => ["queued", "running"].includes(item.status)),
    [history],
  );
  const historyTotalPages = Math.max(1, Math.ceil(historyTotal / HISTORY_PAGE_SIZE));
  const aliasedVideoMedia = useMemo(() => aliasMediaItems(videoMedia), [videoMedia]);

  useEffect(() => {
    let cancelled = false;
    async function checkAuth() {
      if (!getAuthToken()) {
        setAuthorized(false);
        setAuthChecked(true);
        return;
      }
      try {
        await me();
        if (!cancelled) {
          setAuthorized(true);
        }
      } catch {
        if (!cancelled) {
          setAuthorized(false);
        }
      } finally {
        if (!cancelled) {
          setAuthChecked(true);
        }
      }
    }
    void checkAuth();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (authorized) {
      void loadHistory(historyPage);
    }
  }, [authorized, historyPage]);

  useEffect(() => {
    if (!authorized || runningGenerations.length === 0) {
      return;
    }
    const timer = window.setInterval(() => {
      runningGenerations.forEach((item) => void refreshGeneration(item.id));
    }, 3500);
    return () => window.clearInterval(timer);
  }, [authorized, runningGenerations]);

  useEffect(() => {
    videoMediaRef.current = videoMedia;
  }, [videoMedia]);

  useEffect(() => {
    return () => {
      videoMediaRef.current.forEach((item) => {
        if (item.source === "upload") {
          URL.revokeObjectURL(item.previewUrl);
        }
      });
    };
  }, []);

  async function loadHistory(page = historyPage) {
    setLoadingHistory(true);
    setError(null);
    try {
      const result = await listGrokGenerations("all", page, HISTORY_PAGE_SIZE);
      setHistory(result.items);
      setHistoryTotal(result.total);
      if (result.page !== page) {
        setHistoryPage(result.page);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "历史记录加载失败");
    } finally {
      setLoadingHistory(false);
    }
  }

  async function handleImageSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!imagePrompt.trim() || submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await generateGrokImage({
        prompt: imagePrompt.trim(),
        model: imageModel,
        n: imageCount,
        aspect_ratio: imageRatio,
        resolution: imageResolution,
        size: imageSize,
      });
      setHistoryTotal((value) => value + 1);
      if (historyPage === 1) {
        setHistory((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, HISTORY_PAGE_SIZE));
      } else {
        setHistoryPage(1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "图片生成失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleImageEditSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editPrompt.trim() || submitting) {
      return;
    }
    const referenceCount = editImages.length + parseLines(editImageUrls).length + editSourceRefs.length;
    if (referenceCount === 0) {
      setError("图生图需要选择已生成图片、上传图片或填写公网图片 URL");
      return;
    }
    if (referenceCount > IMAGE_EDIT_MAX_REFERENCES) {
      setError("图生图最多支持 3 张输入图片");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await editGrokImage({
        prompt: editPrompt.trim(),
        model: editModel,
        n: editCount,
        aspect_ratio: editRatio,
        resolution: editResolution,
        size: editSize,
        imageUrls: editImageUrls.trim() || undefined,
        sourceRefs: editSourceRefs,
        images: editImages,
      });
      setHistoryTotal((value) => value + 1);
      if (historyPage === 1) {
        setHistory((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, HISTORY_PAGE_SIZE));
      } else {
        setHistoryPage(1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "图生图失败");
    } finally {
      setSubmitting(false);
    }
  }

  function handleEditImageFilesSelected(files: FileList | null) {
    const incoming = Array.from(files ?? []);
    if (editFileInputRef.current) {
      editFileInputRef.current.value = "";
    }
    if (incoming.length === 0) {
      return;
    }
    const usedByOtherSources = parseLines(editImageUrls).length + editSourceRefs.length;
    const maxUploadedImages = Math.max(0, IMAGE_EDIT_MAX_REFERENCES - usedByOtherSources);
    if (maxUploadedImages === 0) {
      setError("图生图输入已满，上传、已生成图片和 URL 合计最多 3 张");
      return;
    }
    const merged = uniqueFiles([...editImages, ...incoming]);
    const nextImages = merged.slice(0, maxUploadedImages);
    setEditImages(nextImages);
    if (merged.length > maxUploadedImages) {
      setError("图生图最多支持 3 张输入图片，已自动保留前 3 张");
    } else {
      setError(null);
    }
  }

  async function handleVideoSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!videoPrompt.trim() || submitting) {
      return;
    }
    const mediaError = validateVideoMedia(videoMode, aliasedVideoMedia);
    if (mediaError) {
      setError(mediaError);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const payload = buildVideoMediaPayload(aliasedVideoMedia);
      const result = await createGrokVideo({
        prompt: videoPrompt.trim(),
        mode: videoMode,
        model: videoModel,
        seconds: videoSeconds,
        duration: videoDuration,
        aspect_ratio: videoRatio,
        resolution: videoResolution,
        images: payload.images,
        video: payload.video,
        mediaManifest: payload.manifest,
      });
      setHistoryTotal((value) => value + 1);
      if (historyPage === 1) {
        setHistory((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, HISTORY_PAGE_SIZE));
      } else {
        setHistoryPage(1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "视频任务创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTalkingPhotoSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    if (!talkingPhotoImage) {
      setError("Talking Photo 需要上传图片");
      return;
    }
    if (!talkingPhotoAudio) {
      setError("Talking Photo 需要上传音频");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await createTalkingPhoto({
        prompt: talkingPhotoPrompt,
        image: talkingPhotoImage,
        audio: talkingPhotoAudio,
      });
      setHistoryTotal((value) => value + 1);
      if (historyPage === 1) {
        setHistory((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, HISTORY_PAGE_SIZE));
      } else {
        setHistoryPage(1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Talking Photo 任务创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeleteGeneration(id: string) {
    if (!window.confirm("删除这条历史记录？")) {
      return;
    }
    setError(null);
    try {
      await deleteGrokGeneration(id);
      setHistory((prev) => prev.filter((item) => item.id !== id));
      setEditSourceRefs((refs) => refs.filter((ref) => !refBelongsToGeneration(ref, id)));
      setVideoMedia((items) => items.filter((item) => !item.ref || !refBelongsToGeneration(item.ref, id)));
      setHistoryTotal((value) => Math.max(0, value - 1));
      if (history.length === 1 && historyPage > 1) {
        setHistoryPage((page) => page - 1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "历史记录删除失败");
    }
  }

  function selectVideoMode(nextMode: VideoMode) {
    setVideoMode(nextMode);
    setVideoMedia((items) => trimVideoMediaForMode(nextMode, items));
    const nextModel = nextMode === "image-to-video" ? "grok-imagine-video-1.5-preview" : "grok-imagine-video";
    setVideoModel(nextModel);
    if (nextModel === "grok-imagine-video-1.5-preview") {
      setVideoResolution("480p");
    }
  }

  function handleVideoModelChange(nextModel: string) {
    setVideoModel(nextModel);
    if (nextModel === "grok-imagine-video-1.5-preview") {
      setVideoMode("image-to-video");
      setVideoResolution("480p");
    }
  }

  function addVideoUploadMedia(kind: MediaKind, files: FileList | null) {
    const incoming = Array.from(files ?? []);
    if (kind === "image" && videoImageFileInputRef.current) {
      videoImageFileInputRef.current.value = "";
    }
    if (kind === "video" && videoVideoFileInputRef.current) {
      videoVideoFileInputRef.current.value = "";
    }
    if (!incoming.length) {
      return;
    }
    const nextItems = incoming.map((file) => uploadMediaItem(kind, file));
    setVideoMedia((items) => trimVideoMediaForMode(videoMode, [...items, ...nextItems]));
    setError(null);
  }

  function addVideoUrlMedia(kind: MediaKind) {
    const value = (kind === "image" ? videoImageUrlInput : videoVideoUrlInput).trim();
    if (!value) {
      return;
    }
    setVideoMedia((items) => trimVideoMediaForMode(videoMode, [...items, urlMediaItem(kind, value)]));
    if (kind === "image") {
      setVideoImageUrlInput("");
    } else {
      setVideoVideoUrlInput("");
    }
    setError(null);
  }

  function addGeneratedVideoMedia(option: GeneratedMediaOption) {
    setVideoMedia((items) =>
      trimVideoMediaForMode(videoMode, [
        ...items.filter((item) => item.ref !== option.ref),
        {
          id: `generated:${option.ref}`,
          kind: option.kind,
          source: "generated",
          ref: option.ref,
          previewUrl: option.src,
          label: option.label,
        },
      ]),
    );
    setError(null);
  }

  function removeVideoMedia(id: string) {
    setVideoMedia((items) => {
      const target = items.find((item) => item.id === id);
      if (target?.source === "upload") {
        URL.revokeObjectURL(target.previewUrl);
      }
      return items.filter((item) => item.id !== id);
    });
  }

  async function refreshGeneration(id: string) {
    try {
      const updated = await refreshGrokGeneration(id);
      setHistory((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    } catch {
      // Keep polling lightweight; explicit refresh will show errors through the backend record.
    }
  }

  if (!authChecked) {
    return (
      <main className="flex h-[100dvh] items-center justify-center bg-[#f2f4f7] text-[#667085]">
        <Loader2 className="mr-2 animate-spin" size={16} />
        加载中
      </main>
    );
  }

  if (!authorized) {
    return (
      <main className="flex h-[100dvh] items-center justify-center bg-[#f2f4f7] px-6 text-[#15191f]">
        <div className="w-full max-w-sm rounded-lg border border-[#d8dee8] bg-white p-6 shadow-sm">
          <div className="text-lg font-semibold">需要登录</div>
          <p className="mt-2 text-sm leading-6 text-[#667085]">请先回到 Rayue 工作台登录，再使用图片和视频生成。</p>
          <a
            href="/"
            className="mt-5 inline-flex h-10 items-center justify-center rounded-md bg-[#15191f] px-4 text-sm font-medium text-white hover:bg-black"
          >
            返回工作台
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="flex h-[100dvh] min-h-0 flex-col overflow-hidden bg-[#eef1f5] text-[#15191f]">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#d8dee8] bg-white px-4 md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <a
            href="/"
            className="flex h-9 w-9 items-center justify-center rounded-md border border-[#d8dee8] text-[#667085] hover:bg-[#f4f6f8] hover:text-[#15191f]"
            aria-label="返回工作台"
          >
            <ArrowLeft size={18} />
          </a>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Sparkles size={18} />
              <h1 className="truncate text-base font-semibold">创作中心</h1>
            </div>
            <p className="truncate text-xs text-[#667085]">图片、视频和口播生成</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void loadHistory()}
          className="flex h-9 items-center gap-2 rounded-md border border-[#d8dee8] bg-white px-3 text-sm text-[#475467] hover:bg-[#f4f6f8]"
        >
          <RefreshCw size={15} className={loadingHistory ? "animate-spin" : ""} />
          刷新
        </button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-0 overflow-hidden lg:grid-cols-[430px_minmax(0,1fr)]">
        <section className="min-h-0 overflow-y-auto border-b border-[#d8dee8] bg-white p-4 lg:border-b-0 lg:border-r lg:p-5">
          <div className="mb-4 grid grid-cols-2 rounded-md bg-[#eef1f5] p-1 sm:grid-cols-4">
            <button
              type="button"
              onClick={() => setMode("image")}
              className={clsx(
                "flex h-10 items-center justify-center gap-2 rounded text-sm font-medium",
                mode === "image" ? "bg-white text-[#15191f] shadow-sm" : "text-[#667085]",
              )}
            >
              <ImagePlus size={16} />
              生图
            </button>
            <button
              type="button"
              onClick={() => setMode("imageEdit")}
              className={clsx(
                "flex h-10 items-center justify-center gap-2 rounded text-sm font-medium",
                mode === "imageEdit" ? "bg-white text-[#15191f] shadow-sm" : "text-[#667085]",
              )}
            >
              <Wand2 size={16} />
              图生图
            </button>
            <button
              type="button"
              onClick={() => setMode("video")}
              className={clsx(
                "flex h-10 items-center justify-center gap-2 rounded text-sm font-medium",
                mode === "video" ? "bg-white text-[#15191f] shadow-sm" : "text-[#667085]",
              )}
            >
              <Video size={16} />
              生视频
            </button>
            <button
              type="button"
              onClick={() => setMode("talkingPhoto")}
              className={clsx(
                "flex h-10 items-center justify-center gap-2 rounded text-sm font-medium",
                mode === "talkingPhoto" ? "bg-white text-[#15191f] shadow-sm" : "text-[#667085]",
              )}
            >
              <AudioLines size={16} />
              口播照
            </button>
          </div>

          {error ? (
            <div className="mb-4 rounded-md border border-[#f2c0b5] bg-[#fff4f2] px-3 py-2 text-sm text-[#9f2a1d]">
              {error}
            </div>
          ) : null}

          {mode === "image" ? (
            <form onSubmit={(event) => void handleImageSubmit(event)} className="space-y-4">
              <PromptBox value={imagePrompt} onChange={setImagePrompt} placeholder="描述你要生成的图片、文字、风格和画幅。" />
              <SelectField label="模型" value={imageModel} onChange={setImageModel} options={IMAGE_MODELS.map((value) => ({ value, label: value }))} />
              {isGptImageModel(imageModel) ? (
                <SelectField label="尺寸" value={imageSize} onChange={setImageSize} options={GPT_IMAGE_SIZES.map((value) => ({ value, label: value }))} />
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <SelectField label="比例" value={imageRatio} onChange={setImageRatio} options={IMAGE_RATIOS.map((value) => ({ value, label: value }))} />
                  <SelectField label="分辨率" value={imageResolution} onChange={setImageResolution} options={[{ value: "1k", label: "1k" }, { value: "2k", label: "2k" }]} />
                </div>
              )}
              <Field label="张数">
                <OptionButtons
                  value={imageCount}
                  onChange={setImageCount}
                  options={IMAGE_COUNTS.map((count) => ({ value: count, label: `${count} 张` }))}
                />
              </Field>
              <SubmitButton loading={submitting} icon={<Wand2 size={16} />}>
                创建图片任务
              </SubmitButton>
            </form>
          ) : mode === "imageEdit" ? (
            <form onSubmit={(event) => void handleImageEditSubmit(event)} className="space-y-4">
              <PromptBox value={editPrompt} onChange={setEditPrompt} placeholder="描述要怎么改图、保留什么、输出什么风格。" />
              <Field label="参考图片">
                <input
                  ref={editFileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={(event) => handleEditImageFilesSelected(event.target.files)}
                  className="block w-full rounded-md border border-[#d8dee8] bg-white px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-[#15191f] file:px-3 file:py-1.5 file:text-sm file:text-white"
                />
                {editImages.length > 0 ? (
                  <div className="mt-2 space-y-2 rounded-md bg-[#eef1f5] px-3 py-2 text-xs text-[#475467]">
                    {editImages.map((file, index) => (
                      <div key={`${file.name}-${file.lastModified}-${index}`} className="flex min-w-0 items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <LocalImageThumb file={file} />
                          <span className="truncate">{file.name}</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setEditImages((prev) => prev.filter((_, fileIndex) => fileIndex !== index))}
                          className="shrink-0 font-medium text-[#15191f] hover:underline"
                        >
                          移除
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => {
                        setEditImages([]);
                        if (editFileInputRef.current) {
                          editFileInputRef.current.value = "";
                        }
                      }}
                      className="pt-1 font-medium text-[#15191f] hover:underline"
                    >
                      清除已选图片
                    </button>
                  </div>
                ) : null}
              </Field>
              <GeneratedImagePicker
                label="已生成图片"
                selectedRefs={editSourceRefs}
                onChange={setEditSourceRefs}
                maxSelected={Math.max(0, IMAGE_EDIT_MAX_REFERENCES - editImages.length - parseLines(editImageUrls).length)}
              />
              <Field label="公网图片 URL">
                <textarea
                  value={editImageUrls}
                  onChange={(event) => setEditImageUrls(event.target.value)}
                  placeholder="可选，每行一个 URL，上传、已生成图片和 URL 合计最多 3 张"
                  className="min-h-[88px] w-full resize-none rounded-md border border-[#d8dee8] px-3 py-2 text-sm leading-6 outline-none focus:border-[#15191f]"
                />
              </Field>
              <SelectField label="模型" value={editModel} onChange={setEditModel} options={IMAGE_MODELS.map((value) => ({ value, label: value }))} />
              {isGptImageModel(editModel) ? (
                <SelectField label="尺寸" value={editSize} onChange={setEditSize} options={GPT_IMAGE_SIZES.map((value) => ({ value, label: value }))} />
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <SelectField label="比例" value={editRatio} onChange={setEditRatio} options={IMAGE_RATIOS.map((value) => ({ value, label: value }))} />
                  <SelectField label="分辨率" value={editResolution} onChange={setEditResolution} options={[{ value: "1k", label: "1k" }, { value: "2k", label: "2k" }]} />
                </div>
              )}
              <Field label="张数">
                <OptionButtons
                  value={editCount}
                  onChange={setEditCount}
                  options={IMAGE_COUNTS.map((count) => ({ value: count, label: `${count} 张` }))}
                />
              </Field>
              <SubmitButton loading={submitting} icon={<Wand2 size={16} />}>
                创建图生图任务
              </SubmitButton>
            </form>
          ) : mode === "video" ? (
            <form onSubmit={(event) => void handleVideoSubmit(event)} className="space-y-4">
              <PromptBox
                value={videoPrompt}
                onChange={setVideoPrompt}
                placeholder="描述视频运动、镜头、主体和风格。可用 @image1 或 @video1 引用下方媒体。"
                mentions={aliasedVideoMedia.map((item) => ({
                  alias: item.alias,
                  kind: item.kind,
                  previewUrl: item.previewUrl,
                }))}
              />
              <Field label="类型">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {VIDEO_MODES.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => selectVideoMode(option.value)}
                      className={clsx(
                        "h-10 rounded-md border px-2 text-sm font-medium",
                        videoMode === option.value
                          ? "border-[#15191f] bg-[#15191f] text-white"
                          : "border-[#d8dee8] bg-white text-[#475467] hover:bg-[#f4f6f8]",
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </Field>
              <SelectField
                label="模型"
                value={videoModel}
                onChange={handleVideoModelChange}
                options={(videoMode === "image-to-video" ? VIDEO_MODELS : VIDEO_MODELS.slice(0, 1)).map((value) => ({ value, label: value }))}
              />
              <div className={clsx("grid gap-3", videoMode === "video-extension" || videoMode === "video-edit" ? "grid-cols-1" : "grid-cols-3")}>
                {videoMode === "video-extension" ? (
                  <Field label="扩展时长">
                    <select
                      value={videoDuration}
                      onChange={(event) => setVideoDuration(Number(event.target.value))}
                      className="h-10 w-full rounded-md border border-[#d8dee8] bg-white px-3 text-sm outline-none focus:border-[#15191f]"
                    >
                      {Array.from({ length: 9 }, (_, index) => index + 2).map((seconds) => (
                        <option key={seconds} value={seconds}>
                          {seconds} 秒
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : videoMode === "video-edit" ? null : (
                  <Field label="时长">
                    <select
                      value={videoSeconds}
                      onChange={(event) => setVideoSeconds(Number(event.target.value))}
                      className="h-10 w-full rounded-md border border-[#d8dee8] bg-white px-3 text-sm outline-none focus:border-[#15191f]"
                    >
                      {VIDEO_SECONDS_OPTIONS.map((seconds) => (
                        <option key={seconds} value={seconds}>
                          {seconds} 秒
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {videoMode === "video-extension" || videoMode === "video-edit" ? null : (
                  <>
                    <SelectField label="比例" value={videoRatio} onChange={setVideoRatio} options={VIDEO_RATIOS.map((value) => ({ value, label: value }))} />
                    <SelectField label="分辨率" value={videoResolution} onChange={setVideoResolution} options={VIDEO_RESOLUTIONS} />
                  </>
                )}
              </div>
              {videoMode !== "text-to-video" ? (
                <Field label="引用媒体">
                  <div className="space-y-3 rounded-md border border-[#d8dee8] bg-[#f7f8fa] p-3">
                    <SelectedMediaStrip items={aliasedVideoMedia} onRemove={removeVideoMedia} />
                    {videoMode === "image-to-video" || videoMode === "reference-to-video" ? (
                      <div className="grid gap-2">
                        <input
                          ref={videoImageFileInputRef}
                          type="file"
                          accept="image/*"
                          multiple={videoMode === "reference-to-video"}
                          onChange={(event) => addVideoUploadMedia("image", event.target.files)}
                          className="block w-full rounded-md border border-[#d8dee8] bg-white px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-[#15191f] file:px-3 file:py-1.5 file:text-sm file:text-white"
                        />
                        <GeneratedMediaPicker kind="image" onChoose={addGeneratedVideoMedia} />
                        <div className="flex gap-2">
                          <input
                            value={videoImageUrlInput}
                            onChange={(event) => setVideoImageUrlInput(event.target.value)}
                            placeholder="https://... 图片 URL"
                            className="h-10 min-w-0 flex-1 rounded-md border border-[#d8dee8] bg-white px-3 text-sm outline-none focus:border-[#15191f]"
                          />
                          <button
                            type="button"
                            onClick={() => addVideoUrlMedia("image")}
                            className="h-10 rounded-md border border-[#d8dee8] bg-white px-3 text-sm font-medium text-[#344054] hover:bg-[#f4f6f8]"
                          >
                            添加
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="grid gap-2">
                        <input
                          ref={videoVideoFileInputRef}
                          type="file"
                          accept="video/mp4,video/*"
                          onChange={(event) => addVideoUploadMedia("video", event.target.files)}
                          className="block w-full rounded-md border border-[#d8dee8] bg-white px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-[#15191f] file:px-3 file:py-1.5 file:text-sm file:text-white"
                        />
                        <GeneratedMediaPicker kind="video" onChoose={addGeneratedVideoMedia} />
                        <div className="flex gap-2">
                          <input
                            value={videoVideoUrlInput}
                            onChange={(event) => setVideoVideoUrlInput(event.target.value)}
                            placeholder="https://... MP4 URL"
                            className="h-10 min-w-0 flex-1 rounded-md border border-[#d8dee8] bg-white px-3 text-sm outline-none focus:border-[#15191f]"
                          />
                          <button
                            type="button"
                            onClick={() => addVideoUrlMedia("video")}
                            className="h-10 rounded-md border border-[#d8dee8] bg-white px-3 text-sm font-medium text-[#344054] hover:bg-[#f4f6f8]"
                          >
                            添加
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </Field>
              ) : null}
              <SubmitButton loading={submitting} icon={<Play size={16} />}>
                创建视频任务
              </SubmitButton>
            </form>
          ) : (
            <form onSubmit={(event) => void handleTalkingPhotoSubmit(event)} className="space-y-4">
              <Field label="人物图片">
                <input
                  ref={talkingPhotoImageInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/*"
                  onChange={(event) => setTalkingPhotoImage(event.target.files?.[0] ?? null)}
                  className="block w-full rounded-md border border-[#d8dee8] bg-white px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-[#15191f] file:px-3 file:py-1.5 file:text-sm file:text-white"
                />
                {talkingPhotoImage ? (
                  <div className="mt-2 flex min-w-0 items-center justify-between gap-2 rounded-md bg-[#eef1f5] px-3 py-2 text-xs text-[#475467]">
                    <div className="flex min-w-0 items-center gap-2">
                      <LocalImageThumb file={talkingPhotoImage} />
                      <span className="truncate">{talkingPhotoImage.name}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setTalkingPhotoImage(null);
                        if (talkingPhotoImageInputRef.current) {
                          talkingPhotoImageInputRef.current.value = "";
                        }
                      }}
                      className="shrink-0 font-medium text-[#15191f] hover:underline"
                    >
                      移除
                    </button>
                  </div>
                ) : null}
              </Field>
              <Field label="音频">
                <input
                  ref={talkingPhotoAudioInputRef}
                  type="file"
                  accept="audio/mpeg,audio/wav,audio/mp4,audio/aac,.mp3,.wav,.m4a,.aac"
                  onChange={(event) => setTalkingPhotoAudio(event.target.files?.[0] ?? null)}
                  className="block w-full rounded-md border border-[#d8dee8] bg-white px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-[#15191f] file:px-3 file:py-1.5 file:text-sm file:text-white"
                />
                {talkingPhotoAudio ? (
                  <div className="mt-2 flex min-w-0 items-center justify-between gap-2 rounded-md bg-[#eef1f5] px-3 py-2 text-xs text-[#475467]">
                    <div className="flex min-w-0 items-center gap-2">
                      <FileAudio size={18} />
                      <span className="truncate">{talkingPhotoAudio.name}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setTalkingPhotoAudio(null);
                        if (talkingPhotoAudioInputRef.current) {
                          talkingPhotoAudioInputRef.current.value = "";
                        }
                      }}
                      className="shrink-0 font-medium text-[#15191f] hover:underline"
                    >
                      移除
                    </button>
                  </div>
                ) : null}
              </Field>
              <PromptBox value={talkingPhotoPrompt} onChange={setTalkingPhotoPrompt} placeholder="口型、表情和说话状态要求，可留空。" />
              <SubmitButton loading={submitting} icon={<AudioLines size={16} />}>
                创建口播视频任务
              </SubmitButton>
            </form>
          )}
        </section>

        <section className="min-h-0 overflow-y-auto p-4 md:p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">历史记录</h2>
              <p className="mt-1 text-xs text-[#667085]">
                每页 {HISTORY_PAGE_SIZE} 条，共 {historyTotal} 条。生成任务会自动轮询。
              </p>
            </div>
            {loadingHistory ? <Loader2 className="animate-spin text-[#667085]" size={18} /> : null}
          </div>

          {history.length === 0 ? (
            <div className="flex min-h-[320px] items-center justify-center rounded-lg border border-dashed border-[#c8d0dc] bg-white/60 text-sm text-[#667085]">
              暂无生成记录
            </div>
          ) : (
            <>
              <div className="grid gap-4 xl:grid-cols-2">
                {history.map((item) => (
                  <HistoryItem
                    key={item.id}
                    item={item}
                    onRefresh={() => void refreshGeneration(item.id)}
                    onDelete={() => void handleDeleteGeneration(item.id)}
                  />
                ))}
              </div>
              <div className="mt-5 flex items-center justify-between border-t border-[#d8dee8] pt-4 text-sm text-[#667085]">
                <button
                  type="button"
                  onClick={() => setHistoryPage((page) => Math.max(1, page - 1))}
                  disabled={historyPage <= 1 || loadingHistory}
                  className="h-9 rounded-md border border-[#d8dee8] bg-white px-3 font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
                >
                  上一页
                </button>
                <span>
                  第 {historyPage} / {historyTotalPages} 页
                </span>
                <button
                  type="button"
                  onClick={() => setHistoryPage((page) => Math.min(historyTotalPages, page + 1))}
                  disabled={historyPage >= historyTotalPages || loadingHistory}
                  className="h-9 rounded-md border border-[#d8dee8] bg-white px-3 font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
                >
                  下一页
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="block">
      <span className="mb-1.5 block text-xs font-medium text-[#475467]">{label}</span>
      {children}
    </div>
  );
}

function OptionButtons<T extends string | number>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div className="grid grid-cols-4 gap-2">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          onClick={() => onChange(option.value)}
          className={clsx(
            "h-10 rounded-md border text-sm font-medium",
            value === option.value
              ? "border-[#15191f] bg-[#15191f] text-white"
              : "border-[#d8dee8] bg-white text-[#475467] hover:bg-[#f4f6f8]",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <Field label={label}>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-[#d8dee8] bg-white px-3 text-sm outline-none focus:border-[#15191f]"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

function PromptBox({
  value,
  onChange,
  placeholder,
  mentions = [],
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  mentions?: PromptMention[];
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const query = mentionQuery(value, textareaRef.current?.selectionStart ?? value.length);
  const suggestions = query === null ? [] : mentions.filter((item) => item.alias.includes(query)).slice(0, 8);

  function insertMention(alias: string) {
    const textarea = textareaRef.current;
    const cursor = textarea?.selectionStart ?? value.length;
    const start = value.lastIndexOf("@", cursor - 1);
    const nextValue = start >= 0 ? `${value.slice(0, start)}@${alias} ${value.slice(cursor)}` : `${value}@${alias} `;
    onChange(nextValue);
    requestAnimationFrame(() => {
      textarea?.focus();
      const nextCursor = (start >= 0 ? start : value.length) + alias.length + 2;
      textarea?.setSelectionRange(nextCursor, nextCursor);
    });
  }

  return (
    <Field label="提示词">
      <div className="relative">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className="min-h-[148px] w-full resize-none rounded-md border border-[#d8dee8] px-3 py-3 text-sm leading-6 outline-none focus:border-[#15191f]"
        />
        {suggestions.length > 0 ? (
          <div className="absolute bottom-3 left-3 z-20 grid max-h-44 w-[min(360px,calc(100%-24px))] gap-1 overflow-y-auto rounded-md border border-[#d8dee8] bg-white p-1 shadow-lg">
            {suggestions.map((item) => (
              <button
                key={item.alias}
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => insertMention(item.alias)}
                className="flex min-w-0 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-[#f4f6f8]"
              >
                <MediaThumb item={{ kind: item.kind, previewUrl: item.previewUrl, label: item.alias }} size="sm" />
                <span className="font-medium">@{item.alias}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </Field>
  );
}

function SubmitButton({ loading, icon, children }: { loading: boolean; icon: ReactNode; children: string }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="flex h-11 w-full items-center justify-center gap-2 rounded-md bg-[#15191f] px-4 text-sm font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:bg-[#98a2b3]"
    >
      {loading ? <Loader2 className="animate-spin" size={16} /> : icon}
      {children}
    </button>
  );
}

function GeneratedImagePicker({
  label,
  selectedRefs,
  onChange,
  maxSelected,
}: {
  label: string;
  selectedRefs: string[];
  onChange: (refs: string[]) => void;
  maxSelected: number;
}) {
  const [open, setOpen] = useState(false);
  const canOpen = maxSelected > 0 || selectedRefs.length > 0;

  return (
    <Field label={label}>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(true)}
          disabled={!canOpen}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-[#d8dee8] bg-white px-3 text-sm font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
        >
          <Images size={16} />
          选择已生成图片
        </button>
        <span className="text-xs text-[#667085]">
          已选 {selectedRefs.length} 张，最多 {maxSelected} 张
        </span>
        {selectedRefs.length > 0 ? (
          <button type="button" onClick={() => onChange([])} className="text-xs font-medium text-[#15191f] hover:underline">
            清除
          </button>
        ) : null}
      </div>
      {selectedRefs.length > 0 ? (
        <div className="mt-2 flex gap-2 overflow-x-auto">
          {selectedRefs.map((ref) => (
            <div key={ref} className="h-14 w-14 shrink-0 overflow-hidden rounded-md border border-[#d8dee8] bg-white">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={grokDownloadUrl(...refToDownloadParts(ref))} alt="已选图片" className="h-full w-full object-cover" />
            </div>
          ))}
        </div>
      ) : null}
      {open ? (
        <GeneratedImageModal
          selectedRefs={selectedRefs}
          onChange={onChange}
          maxSelected={maxSelected}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </Field>
  );
}

function GeneratedImageModal({
  selectedRefs,
  onChange,
  maxSelected,
  onClose,
}: {
  selectedRefs: string[];
  onChange: (refs: string[]) => void;
  maxSelected: number;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<GrokGeneration[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
  const options = useMemo(() => collectGeneratedImages(items), [items]);

  useEffect(() => {
    let cancelled = false;
    async function loadImages() {
      setLoading(true);
      setModalError(null);
      try {
        const result = await listGrokGenerations("image", page, HISTORY_PAGE_SIZE, "succeeded");
        if (!cancelled) {
          setItems(result.items);
          setTotal(result.total);
        }
      } catch (err) {
        if (!cancelled) {
          setModalError(err instanceof Error ? err.message : "图片加载失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void loadImages();
    return () => {
      cancelled = true;
    };
  }, [page]);

  function toggle(ref: string) {
    if (selectedRefs.includes(ref)) {
      onChange(selectedRefs.filter((item) => item !== ref));
      return;
    }
    if (selectedRefs.length >= maxSelected) {
      return;
    }
    onChange(maxSelected === 1 ? [ref] : [...selectedRefs, ref]);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4 py-6" role="dialog" aria-modal="true">
      <div className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex shrink-0 items-center justify-between border-b border-[#edf0f4] px-4 py-3">
          <div>
            <div className="text-sm font-semibold">选择已生成图片</div>
            <div className="mt-1 text-xs text-[#667085]">
              已选 {selectedRefs.length} 张，最多 {maxSelected} 张
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-md text-[#667085] hover:bg-[#f4f6f8] hover:text-[#15191f]"
            aria-label="关闭"
          >
            <X size={17} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {modalError ? (
            <div className="mb-3 rounded-md border border-[#f2c0b5] bg-[#fff4f2] px-3 py-2 text-sm text-[#9f2a1d]">
              {modalError}
            </div>
          ) : null}
          {loading ? (
            <div className="flex min-h-[300px] items-center justify-center text-sm text-[#667085]">
              <Loader2 className="mr-2 animate-spin" size={16} />
              加载图片
            </div>
          ) : options.length === 0 ? (
            <div className="flex min-h-[300px] items-center justify-center rounded-md border border-dashed border-[#c8d0dc] bg-[#f7f8fa] text-sm text-[#667085]">
              暂无可选图片
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {options.map((option) => {
                const selected = selectedRefs.includes(option.ref);
                const disabled = !selected && selectedRefs.length >= maxSelected;
                return (
                  <button
                    key={option.ref}
                    type="button"
                    onClick={() => toggle(option.ref)}
                    disabled={disabled}
                    className={clsx(
                      "group relative overflow-hidden rounded-md border bg-white text-left disabled:cursor-not-allowed disabled:opacity-45",
                      selected ? "border-[#15191f] ring-2 ring-[#15191f]" : "border-[#d8dee8] hover:border-[#98a2b3]",
                    )}
                    title={`${option.model} - ${option.label}`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={option.src} alt={option.label} className="aspect-square w-full object-cover" />
                    <div className="truncate px-2 py-1.5 text-[11px] text-[#667085]">{option.model}</div>
                    {selected ? (
                      <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-[#15191f] text-white">
                        <Check size={15} />
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center justify-between border-t border-[#edf0f4] px-4 py-3 text-sm text-[#667085]">
          <button
            type="button"
            onClick={() => setPage((value) => Math.max(1, value - 1))}
            disabled={page <= 1 || loading}
            className="h-9 rounded-md border border-[#d8dee8] bg-white px-3 font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
          >
            上一页
          </button>
          <span>
            第 {page} / {totalPages} 页，共 {total} 条
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
              disabled={page >= totalPages || loading}
              className="h-9 rounded-md border border-[#d8dee8] bg-white px-3 font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
            >
              下一页
            </button>
            <button
              type="button"
              onClick={onClose}
              className="h-9 rounded-md bg-[#15191f] px-4 font-medium text-white hover:bg-black"
            >
              完成
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function GeneratedMediaPicker({ kind, onChoose }: { kind: MediaKind; onChoose: (option: GeneratedMediaOption) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-[#d8dee8] bg-white px-3 text-sm font-medium text-[#344054] hover:bg-[#f4f6f8]"
      >
        <Images size={16} />
        选择历史{kind === "image" ? "图片" : "视频"}
      </button>
      {open ? (
        <GeneratedMediaModal
          kind={kind}
          onChoose={(option) => {
            onChoose(option);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

function GeneratedMediaModal({
  kind,
  onChoose,
  onClose,
}: {
  kind: MediaKind;
  onChoose: (option: GeneratedMediaOption) => void;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<GrokGeneration[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
  const options = useMemo(() => collectGeneratedMedia(items, kind), [items, kind]);

  useEffect(() => {
    let cancelled = false;
    async function loadMedia() {
      setLoading(true);
      setModalError(null);
      try {
        const result = await listGrokGenerations(kind, page, HISTORY_PAGE_SIZE, "succeeded");
        if (!cancelled) {
          setItems(result.items);
          setTotal(result.total);
        }
      } catch (err) {
        if (!cancelled) {
          setModalError(err instanceof Error ? err.message : "媒体加载失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void loadMedia();
    return () => {
      cancelled = true;
    };
  }, [kind, page]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4 py-6" role="dialog" aria-modal="true">
      <div className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex shrink-0 items-center justify-between border-b border-[#edf0f4] px-4 py-3">
          <div className="text-sm font-semibold">选择历史{kind === "image" ? "图片" : "视频"}</div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-md text-[#667085] hover:bg-[#f4f6f8] hover:text-[#15191f]"
            aria-label="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {modalError ? <div className="mb-3 rounded-md border border-[#f2c0b5] bg-[#fff4f2] px-3 py-2 text-sm text-[#9f2a1d]">{modalError}</div> : null}
          {loading ? (
            <div className="flex min-h-[300px] items-center justify-center text-sm text-[#667085]">
              <Loader2 className="mr-2 animate-spin" size={16} />
              加载媒体
            </div>
          ) : options.length === 0 ? (
            <div className="flex min-h-[300px] items-center justify-center rounded-md border border-dashed border-[#c8d0dc] bg-[#f7f8fa] text-sm text-[#667085]">
              暂无可选媒体
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {options.map((option) => (
                <button
                  key={option.ref}
                  type="button"
                  onClick={() => onChoose(option)}
                  className="group overflow-hidden rounded-md border border-[#d8dee8] bg-white text-left hover:border-[#98a2b3]"
                  title={`${option.model} - ${option.label}`}
                >
                  {option.kind === "video" ? (
                    <video src={option.src} muted className="aspect-square w-full bg-black object-cover" />
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={option.src} alt={option.label} className="aspect-square w-full object-cover" />
                  )}
                  <div className="truncate px-2 py-1.5 text-[11px] text-[#667085]">{option.model}</div>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center justify-between border-t border-[#edf0f4] px-4 py-3 text-sm text-[#667085]">
          <button
            type="button"
            onClick={() => setPage((value) => Math.max(1, value - 1))}
            disabled={page <= 1 || loading}
            className="h-9 rounded-md border border-[#d8dee8] bg-white px-3 font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
          >
            上一页
          </button>
          <span>
            第 {page} / {totalPages} 页，共 {total} 条
          </span>
          <button
            type="button"
            onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
            disabled={page >= totalPages || loading}
            className="h-9 rounded-md border border-[#d8dee8] bg-white px-3 font-medium text-[#344054] hover:bg-[#f4f6f8] disabled:cursor-not-allowed disabled:opacity-45"
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}

function SelectedMediaStrip({ items, onRemove }: { items: AliasedMediaItem[]; onRemove: (id: string) => void }) {
  if (items.length === 0) {
    return <div className="rounded-md border border-dashed border-[#c8d0dc] bg-white px-3 py-4 text-center text-sm text-[#667085]">未选择媒体</div>;
  }
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {items.map((item) => (
        <div key={item.id} className="relative w-20 shrink-0 overflow-hidden rounded-md border border-[#d8dee8] bg-white">
          <MediaThumb item={item} />
          <div className="truncate px-1.5 py-1 text-center text-[11px] font-medium text-[#344054]">{item.alias}</div>
          <button
            type="button"
            onClick={() => onRemove(item.id)}
            className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-black/70 text-white"
            aria-label={`移除 ${item.alias}`}
          >
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

function MediaThumb({ item, size = "md" }: { item: Pick<VideoMediaItem, "kind" | "previewUrl" | "label">; size?: "sm" | "md" }) {
  const className = clsx(size === "sm" ? "h-8 w-8" : "h-20 w-full", "bg-[#eef1f5] object-cover");
  if (item.kind === "video") {
    return <video src={item.previewUrl} muted className={className} />;
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={item.previewUrl} alt={item.label} className={className} />;
}

function LocalImageThumb({ file }: { file: File }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    const url = URL.createObjectURL(file);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  if (!src) {
    return <span className="h-9 w-9 shrink-0 rounded bg-white" />;
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={file.name} className="h-9 w-9 shrink-0 rounded object-cover" />;
}

function HistoryItem({
  item,
  onRefresh,
  onDelete,
}: {
  item: GrokGeneration;
  onRefresh: () => void;
  onDelete: () => void;
}) {
  const output = item.outputs[0];
  const previewUrl = output ? grokDownloadUrl(item.id, 0) : null;
  const isTalkingPhoto = item.model === "talking-photo" || item.params.mode === "talking-photo";
  const isVideo = item.kind === "video";
  const running = ["queued", "running"].includes(item.status);
  const title = isTalkingPhoto ? "Talking Photo" : isVideo ? "视频" : item.params.mode === "image-to-image" ? "图生图" : "图片";

  return (
    <article className="overflow-hidden rounded-lg border border-[#d8dee8] bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-[#edf0f4] px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold">
            {isTalkingPhoto ? <AudioLines size={15} /> : isVideo ? <Video size={15} /> : <ImagePlus size={15} />}
            <span className="truncate">{title}</span>
            <span
              className={clsx(
                "rounded-full px-2 py-0.5 text-xs font-medium",
                item.status === "succeeded" && "bg-[#e7f7ef] text-[#087443]",
                running && "bg-[#eef4ff] text-[#175cd3]",
                item.status === "failed" && "bg-[#fff1f3] text-[#c01048]",
              )}
            >
              {statusLabel[item.status] ?? item.status}
            </span>
          </div>
          <div className="mt-1 truncate text-xs text-[#667085]">{item.model}</div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {running ? (
            <button
              type="button"
              onClick={onRefresh}
              className="flex h-8 w-8 items-center justify-center rounded-md text-[#667085] hover:bg-[#f4f6f8] hover:text-[#15191f]"
              title="刷新状态"
            >
              <RefreshCw size={15} />
            </button>
          ) : null}
          <button
            type="button"
            onClick={onDelete}
            className="flex h-8 w-8 items-center justify-center rounded-md text-[#667085] hover:bg-[#fff1f3] hover:text-[#c01048]"
            title="删除记录"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      <div className="bg-[#f7f8fa]">
        {previewUrl && output ? (
          isVideo ? (
            <video src={previewUrl} controls className="aspect-video w-full bg-black object-contain" />
          ) : item.outputs.length > 1 ? (
            <div className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2">
              {item.outputs.map((image, index) => (
                <a
                  key={`${image.name}-${index}`}
                  href={grokDownloadUrl(item.id, index)}
                  className="group block overflow-hidden rounded-md border border-[#d8dee8] bg-white"
                  title={`下载 ${image.name}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={grokDownloadUrl(item.id, index)}
                    alt={`生成图片 ${index + 1}`}
                    className="aspect-square w-full object-contain transition group-hover:scale-[1.01]"
                  />
                </a>
              ))}
            </div>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={previewUrl} alt="生成图片" className="max-h-[460px] w-full object-contain" />
          )
        ) : (
          <div className="flex aspect-video items-center justify-center text-sm text-[#667085]">
            {running ? (
              <span className="flex items-center gap-2">
                <Loader2 className="animate-spin" size={16} />
                生成中 {typeof item.params.progress === "number" ? `${item.params.progress}%` : ""}
              </span>
            ) : (
              item.error || "暂无输出"
            )}
          </div>
        )}
      </div>

      <div className="space-y-3 p-4">
        <p className="line-clamp-3 text-sm leading-6 text-[#344054]">{item.prompt}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-[#667085]">
          {Object.entries(item.params ?? {}).slice(0, 4).map(([key, value]) => (
            <span key={key} className="rounded bg-[#eef1f5] px-2 py-1">
              {key}: {String(value)}
            </span>
          ))}
        </div>
        {item.error ? <div className="rounded bg-[#fff1f3] px-3 py-2 text-xs text-[#c01048]">{item.error}</div> : null}
        {item.outputs.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {item.outputs.map((file, index) => (
              <a
                key={`${file.name}-download-${index}`}
                href={grokDownloadUrl(item.id, index)}
                className="inline-flex h-9 items-center gap-2 rounded-md border border-[#d8dee8] px-3 text-sm font-medium text-[#344054] hover:bg-[#f4f6f8]"
              >
                <Download size={15} />
                {item.outputs.length > 1 ? `下载 ${index + 1}` : "下载"} {formatBytes(file.size)}
              </a>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}

function isGptImageModel(model: string) {
  return model === "gpt-image-2";
}

function collectGeneratedImages(items: GrokGeneration[]): GeneratedImageOption[] {
  return items.flatMap((item) => {
    if (item.kind !== "image" || item.status !== "succeeded") {
      return [];
    }
    return item.outputs
      .map((output, index) => {
        if (!output.media_type.startsWith("image/")) {
          return null;
        }
        return {
          ref: `${item.id}:${index}`,
          src: grokDownloadUrl(item.id, index),
          label: item.prompt || output.name,
          model: item.model,
        };
      })
      .filter((option): option is GeneratedImageOption => Boolean(option));
  });
}

function collectGeneratedMedia(items: GrokGeneration[], kind: MediaKind): GeneratedMediaOption[] {
  return items.flatMap((item) => {
    if (item.kind !== kind || item.status !== "succeeded") {
      return [];
    }
    return item.outputs
      .map((output, index) => {
        if (!output.media_type.startsWith(`${kind}/`)) {
          return null;
        }
        return {
          ref: `${item.id}:${index}`,
          src: grokDownloadUrl(item.id, index),
          label: item.prompt || output.name,
          model: item.model,
          kind,
        };
      })
      .filter((option): option is GeneratedMediaOption => Boolean(option));
  });
}

function aliasMediaItems(items: VideoMediaItem[]): AliasedMediaItem[] {
  let imageIndex = 0;
  let videoIndex = 0;
  return items.map((item) => {
    if (item.kind === "image") {
      imageIndex += 1;
      return { ...item, alias: `image${imageIndex}` };
    }
    videoIndex += 1;
    return { ...item, alias: `video${videoIndex}` };
  });
}

function validateVideoMedia(mode: VideoMode, items: AliasedMediaItem[]) {
  const images = items.filter((item) => item.kind === "image");
  const videos = items.filter((item) => item.kind === "video");
  if (mode === "text-to-video") {
    return items.length > 0 ? "文生视频不需要引用媒体" : null;
  }
  if (mode === "image-to-video") {
    return images.length === 1 && videos.length === 0 ? null : "单图首帧需要且只能选择 1 张图片";
  }
  if (mode === "reference-to-video") {
    return images.length >= 1 && images.length <= VIDEO_REFERENCE_MAX && videos.length === 0 ? null : "多参考图需要 1 到 7 张图片";
  }
  if (mode === "video-extension" || mode === "video-edit") {
    return videos.length === 1 && images.length === 0 ? null : "视频编辑/扩展需要且只能选择 1 个视频";
  }
  return null;
}

function buildVideoMediaPayload(items: AliasedMediaItem[]) {
  const uploadImages = items.filter((item) => item.kind === "image" && item.source === "upload" && item.file);
  const uploadVideo = items.find((item) => item.kind === "video" && item.source === "upload" && item.file);
  const manifest = items.map((item) => {
    const base = { kind: item.kind, source: item.source, alias: item.alias };
    if (item.source === "upload") {
      const index = item.kind === "image" ? uploadImages.findIndex((upload) => upload.id === item.id) : 0;
      return { ...base, index, order: items.indexOf(item) + 1 };
    }
    if (item.source === "generated") {
      return { ...base, ref: item.ref };
    }
    return { ...base, url: item.url };
  });
  return {
    images: uploadImages.map((item) => item.file).filter((file): file is File => Boolean(file)),
    video: uploadVideo?.file ?? null,
    manifest,
  };
}

function uploadMediaItem(kind: MediaKind, file: File): VideoMediaItem {
  return {
    id: `upload:${kind}:${file.name}:${file.size}:${file.lastModified}:${localId()}`,
    kind,
    source: "upload",
    file,
    previewUrl: URL.createObjectURL(file),
    label: file.name,
  };
}

function urlMediaItem(kind: MediaKind, url: string): VideoMediaItem {
  return {
    id: `url:${kind}:${url}:${localId()}`,
    kind,
    source: "url",
    url,
    previewUrl: url,
    label: url,
  };
}

function localId() {
  return globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
}

function trimVideoMediaForMode(mode: VideoMode, items: VideoMediaItem[]) {
  const images = items.filter((item) => item.kind === "image");
  const videos = items.filter((item) => item.kind === "video");
  if (mode === "image-to-video") {
    return images.slice(-1);
  }
  if (mode === "reference-to-video") {
    return images.slice(0, VIDEO_REFERENCE_MAX);
  }
  if (mode === "video-extension" || mode === "video-edit") {
    return videos.slice(-1);
  }
  return [];
}

function mentionQuery(value: string, cursor: number) {
  const start = value.lastIndexOf("@", cursor - 1);
  if (start < 0) {
    return null;
  }
  const prefix = value.slice(start + 1, cursor);
  if (/\s/.test(prefix) || prefix.length > 24) {
    return null;
  }
  return prefix;
}

function parseLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function uniqueFiles(files: File[]) {
  const seen = new Set<string>();
  return files.filter((file) => {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function refBelongsToGeneration(ref: string, generationId: string) {
  return ref.split(":", 1)[0] === generationId;
}

function refToDownloadParts(ref: string): [string, number] {
  const [id, index] = ref.split(":", 2);
  return [id, Number(index || 0)];
}

function formatBytes(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
