from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://rayue_agent:rayue_agent@localhost:55432/rayue_agent"
    frontend_origin: str = "http://localhost:3000"

    agent_run_mode: Literal["e2b", "mock"] = "e2b"
    e2b_api_key: str | None = None
    e2b_template: str = "rayue-agent-v2"
    sandbox_idle_timeout_seconds: int = 900
    sandbox_workspace: str = "/home/user/workspace"
    sandbox_allow_internet: bool = True

    model_base_url: str = "http://47.90.255.159:18080/v1"
    model_name: str = "gpt-5.5"
    model_provider_id: str = "custom"
    codex_api_key: str | None = Field(default=None, validation_alias="CODEX_API_KEY")
    codex_web_search: Literal["disabled", "cached", "live"] = "cached"
    gpt_image2_base_url: str = "http://47.90.255.159:18080"
    gpt_image2_api_key: str | None = Field(default=None, validation_alias="GPT_IMAGE2_API_KEY")
    gpt_image2_generate_path: str = "/v1/images/generations"
    gpt_image2_edit_path: str = "/v1/images/edits"
    gpt_image2_timeout_seconds: int = 3600
    convertx_service_url: str = "http://127.0.0.1:8072"
    convertx_api_base_url: str | None = None
    convertx_timeout_seconds: int = 900

    admin_skills_dir: Path = PROJECT_ROOT / "backend" / "agent_skills"
    skill_bundle_dir: Path = PROJECT_ROOT / "storage" / "skill-bundles"
    upload_storage_dir: Path = PROJECT_ROOT / "storage" / "uploads"
    upload_bundle_dir: Path = PROJECT_ROOT / "storage" / "upload-bundles"
    sandbox_inputs_dir: str = "inputs"
    max_upload_file_bytes: int = 150 * 1024 * 1024
    max_upload_files_per_request: int = 10
    artifact_storage_dir: Path = PROJECT_ROOT / "storage" / "artifacts"
    max_artifact_bytes: int = 1024 * 1024 * 1024
    s3_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RAYUE_S3_BUCKET", "XPET_S3_BUCKET", "S3_BUCKET"),
    )
    s3_region: str = Field(
        default="auto",
        validation_alias=AliasChoices("RAYUE_S3_REGION", "XPET_S3_REGION", "S3_REGION"),
    )
    s3_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RAYUE_S3_ENDPOINT", "XPET_S3_ENDPOINT", "S3_ENDPOINT"),
    )
    s3_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RAYUE_S3_ACCESS_KEY", "XPET_S3_ACCESS_KEY", "S3_ACCESS_KEY"),
    )
    s3_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RAYUE_S3_SECRET_KEY", "XPET_S3_SECRET_KEY", "S3_SECRET_KEY"),
    )
    object_storage_prefix: str = "rayue-agent"
    object_storage_presign_seconds: int = 3600
    log_codex_raw_events: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
