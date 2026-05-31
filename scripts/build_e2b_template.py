#!/usr/bin/env python3
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from e2b import Template
from e2b import default_build_logger

from app.config import get_settings
from app.skills import build_skill_bundle


TEMPLATE_NAME = os.environ.get("RAYUE_TEMPLATE_NAME", "rayue-agent-v2")
BASE_TEMPLATE = os.environ.get("RAYUE_TEMPLATE_BASE", "codex")
CODEX_PACKAGE = os.environ.get("RAYUE_CODEX_PACKAGE", "@openai/codex@0.135.0")

APT_PACKAGES = [
    "build-essential",
    "ca-certificates",
    "curl",
    "default-jre-headless",
    "ffmpeg",
    "fd-find",
    "file",
    "fonts-dejavu",
    "fonts-freefont-ttf",
    "fonts-liberation",
    "fonts-noto-cjk",
    "fonts-noto-color-emoji",
    "ghostscript",
    "git",
    "git-lfs",
    "imagemagick",
    "jq",
    "less",
    "libcairo2-dev",
    "libjpeg-dev",
    "libpango1.0-dev",
    "libpng-dev",
    "libreoffice",
    "libreoffice-java-common",
    "libreoffice-l10n-zh-cn",
    "pandoc",
    "pkg-config",
    "poppler-utils",
    "python3-pip",
    "python3-venv",
    "qpdf",
    "ripgrep",
    "rsync",
    "shellcheck",
    "sqlite3",
    "tesseract-ocr",
    "tesseract-ocr-chi-sim",
    "tree",
    "unzip",
    "webp",
    "xvfb",
    "zip",
]

PYTHON_PACKAGES = [
    "aiohttp",
    "beautifulsoup4",
    "black",
    "docx2python",
    "duckdb",
    "html5lib",
    "httpx",
    "imageio",
    "jinja2",
    "lxml",
    "markdown",
    "markdownify",
    "markitdown[pdf,pptx,docx,xlsx]",
    "matplotlib",
    "moviepy",
    "networkx",
    "numpy",
    "openpyxl",
    "opencv-python-headless",
    "pandas",
    "pdf2image",
    "pdfplumber",
    "pillow",
    "playwright",
    "pyarrow",
    "pydub",
    "pypdf",
    "pytest",
    "python-docx",
    "python-magic",
    "python-pptx",
    "pytesseract",
    "reportlab",
    "requests",
    "rich",
    "ruff",
    "scipy",
    "scikit-image",
    "seaborn",
    "statsmodels",
    "sympy",
    "tabulate",
    "typer",
    "weasyprint",
    "xlsxwriter",
    "brotli",
    "curl_cffi",
    "mutagen",
    "pycryptodomex",
    "websockets",
    "yt-dlp[default,curl-cffi]",
    "yt-dlp-ejs",
]

NODE_PACKAGES = [
    "@mermaid-js/mermaid-cli",
    "@types/node",
    "canvas",
    "cheerio@1.0.0-rc.12",
    "docx",
    "exceljs",
    "jsdom@24.1.3",
    "lucide-react",
    "markdown-it",
    "pdf-lib",
    "pptxgenjs",
    "prettier",
    "react",
    "react-dom",
    "sharp",
    "tsx",
    "typescript",
    "vite@5.4.21",
    "xlsx",
    "yargs@17.7.2",
]

GLOBAL_NODE_PACKAGES = [
    "@mermaid-js/mermaid-cli",
    "prettier",
    "tsx",
    "typescript",
]

WORKSPACE_NODE_PACKAGES = [*NODE_PACKAGES, "playwright@1.60.0"]


def sh_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main() -> None:
    settings = get_settings()
    if settings.e2b_api_key and not os.environ.get("E2B_API_KEY"):
        os.environ["E2B_API_KEY"] = settings.e2b_api_key
    if not os.environ.get("E2B_API_KEY"):
        raise RuntimeError("E2B_API_KEY is required to build the Rayue sandbox template")

    bundle = build_skill_bundle(settings)
    bundle_remote_path = "/tmp/rayue-admin-skills.tar.gz"
    skills_dir = "/home/user/.codex/skills"
    marker_path = f"{skills_dir}/.rayue-agent-skills.sha256"

    bundle_relative_path = bundle.path.relative_to(PROJECT_ROOT)
    apply_patch_relative_path = Path("backend/sandbox_tools/apply_patch.py")

    template = (
        Template(file_context_path=PROJECT_ROOT)
        .from_template(BASE_TEMPLATE)
        .apt_install(APT_PACKAGES, no_install_recommends=True)
        .pip_install(PYTHON_PACKAGES, g=True)
        .run_cmd(f"npm install -g --force {sh_single_quote(CODEX_PACKAGE)}")
        .run_cmd(f"npm install -g --force {' '.join(GLOBAL_NODE_PACKAGES)}")
        .run_cmd("python -m playwright install --with-deps chromium")
        .copy(apply_patch_relative_path, "/tmp/rayue-apply-patch")
        .run_cmd(
            "set -e; "
            "install -m 0755 /tmp/rayue-apply-patch /usr/local/bin/apply_patch; "
            "ln -sf /usr/bin/fdfind /usr/local/bin/fd; "
            "ln -sf $(command -v python3) /usr/local/bin/python; "
            "git lfs install --system >/dev/null 2>&1 || true"
        )
        .run_cmd("mkdir -p /home/user/workspace /home/user/.codex/skills && chown -R user:user /home/user")
        .run_cmd(
            "cd /home/user/workspace && "
            "npm init -y >/dev/null 2>&1 && "
            f"npm install {' '.join(WORKSPACE_NODE_PACKAGES)}",
            user="user",
        )
        .copy(bundle_relative_path, bundle_remote_path)
        .run_cmd(
            "set -e; "
            f"rm -rf {sh_single_quote(skills_dir)}; "
            f"mkdir -p {sh_single_quote(skills_dir)}; "
            f"tar -xzf {sh_single_quote(bundle_remote_path)} -C {sh_single_quote(skills_dir)}; "
            f"printf %s {sh_single_quote(bundle.sha256)} > {sh_single_quote(marker_path)}; "
            "chown -R user:user /home/user/.codex /home/user/workspace",
        )
        .set_envs(
            {
                "NODE_PATH": "/home/user/workspace/node_modules:/usr/local/lib/node_modules",
                "PAGER": "cat",
                "PLAYWRIGHT_BROWSERS_PATH": "/home/user/.cache/ms-playwright",
            }
        )
        .set_workdir("/home/user/workspace")
    )

    print(
        f"Building E2B template {TEMPLATE_NAME!r} from {BASE_TEMPLATE!r} "
        f"with Codex package {CODEX_PACKAGE!r}, "
        f"{bundle.skill_count} skills and {len(APT_PACKAGES) + len(PYTHON_PACKAGES) + 1 + len(GLOBAL_NODE_PACKAGES) + len(WORKSPACE_NODE_PACKAGES)} packages."
    )
    Template.build(
        template,
        TEMPLATE_NAME,
        cpu_count=4,
        memory_mb=4096,
        on_build_logs=default_build_logger(),
    )
    print(f"Built E2B template: {TEMPLATE_NAME}")


if __name__ == "__main__":
    main()
