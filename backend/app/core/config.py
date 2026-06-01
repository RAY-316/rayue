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
    sandbox_workspace_mount_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("RAYUE_SANDBOX_WORKSPACE_MOUNT_ENABLED", "SANDBOX_WORKSPACE_MOUNT_ENABLED"),
    )
    sandbox_workspace_mount_path: str = Field(
        default="/mnt/rayue-workspace",
        validation_alias=AliasChoices("RAYUE_SANDBOX_WORKSPACE_MOUNT_PATH", "SANDBOX_WORKSPACE_MOUNT_PATH"),
    )
    sandbox_workspace_mount_cache_path: str = Field(
        default="/tmp/rayue-rclone-cache",
        validation_alias=AliasChoices("RAYUE_SANDBOX_WORKSPACE_MOUNT_CACHE_PATH", "SANDBOX_WORKSPACE_MOUNT_CACHE_PATH"),
    )
    r2_temp_credentials_ttl_seconds: int = Field(
        default=10800,
        validation_alias=AliasChoices("RAYUE_R2_TEMP_CREDENTIALS_TTL_SECONDS", "R2_TEMP_CREDENTIALS_TTL_SECONDS"),
    )

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
    max_upload_file_bytes: int = 1024 * 1024 * 1024
    max_upload_files_per_request: int = 10
    artifact_storage_dir: Path = PROJECT_ROOT / "storage" / "artifacts"
    max_artifact_bytes: int = 1024 * 1024 * 1024
    max_workspaces_per_user: int = 3
    workspace_limit_bytes: int = 1024 * 1024 * 1024

    email_delivery_mode: str = Field(
        default="mock",
        validation_alias=AliasChoices("RAYUE_EMAIL_DELIVERY_MODE", "XPET_EMAIL_DELIVERY_MODE", "EMAIL_DELIVERY_MODE"),
    )
    email_code_ttl_minutes: int = Field(default=10, validation_alias=AliasChoices("RAYUE_EMAIL_CODE_TTL_MINUTES", "XPET_EMAIL_CODE_TTL_MINUTES"))
    email_code_cooldown_seconds: int = Field(default=60, validation_alias=AliasChoices("RAYUE_EMAIL_CODE_COOLDOWN_SECONDS", "XPET_EMAIL_CODE_COOLDOWN_SECONDS"))
    email_code_hourly_limit: int = Field(default=20, validation_alias=AliasChoices("RAYUE_EMAIL_CODE_HOURLY_LIMIT", "XPET_EMAIL_CODE_HOURLY_LIMIT"))
    email_code_source_hourly_limit: int = Field(default=60, validation_alias=AliasChoices("RAYUE_EMAIL_CODE_SOURCE_HOURLY_LIMIT", "EMAIL_CODE_SOURCE_HOURLY_LIMIT"))
    captcha_ttl_seconds: int = Field(default=300, validation_alias=AliasChoices("RAYUE_CAPTCHA_TTL_SECONDS", "CAPTCHA_TTL_SECONDS"))
    captcha_length: int = Field(default=4, validation_alias=AliasChoices("RAYUE_CAPTCHA_LENGTH", "CAPTCHA_LENGTH"))
    captcha_attempt_limit: int = Field(default=5, validation_alias=AliasChoices("RAYUE_CAPTCHA_ATTEMPT_LIMIT", "CAPTCHA_ATTEMPT_LIMIT"))
    captcha_source_hourly_limit: int = Field(default=120, validation_alias=AliasChoices("RAYUE_CAPTCHA_SOURCE_HOURLY_LIMIT", "CAPTCHA_SOURCE_HOURLY_LIMIT"))
    session_ttl_days: int = Field(default=30, validation_alias=AliasChoices("RAYUE_SESSION_TTL_DAYS", "XPET_SESSION_TTL_DAYS"))
    resend_api_key: str | None = Field(default=None, validation_alias=AliasChoices("RAYUE_RESEND_API_KEY", "XPET_RESEND_API_KEY", "RESEND_API_KEY"))
    resend_from_email: str = Field(default="noreply@mail.omnedesk.com", validation_alias=AliasChoices("RAYUE_RESEND_FROM_EMAIL", "XPET_RESEND_FROM_EMAIL", "RESEND_FROM_EMAIL"))
    resend_api_base_url: str = Field(default="https://api.resend.com", validation_alias=AliasChoices("RAYUE_RESEND_API_BASE_URL", "XPET_RESEND_API_BASE_URL", "RESEND_API_BASE_URL"))
    tencent_smtp_host: str = Field(default="smtp.qcloudmail.com", validation_alias=AliasChoices("RAYUE_TENCENT_SMTP_HOST", "XPET_TENCENT_SMTP_HOST", "TENCENT_SMTP_HOST"))
    tencent_smtp_port: int = Field(default=465, validation_alias=AliasChoices("RAYUE_TENCENT_SMTP_PORT", "XPET_TENCENT_SMTP_PORT", "TENCENT_SMTP_PORT"))
    tencent_smtp_username: str | None = Field(default=None, validation_alias=AliasChoices("RAYUE_TENCENT_SMTP_USERNAME", "XPET_TENCENT_SMTP_USERNAME", "TENCENT_SMTP_USERNAME"))
    tencent_smtp_password: str | None = Field(default=None, validation_alias=AliasChoices("RAYUE_TENCENT_SMTP_PASSWORD", "XPET_TENCENT_SMTP_PASSWORD", "TENCENT_SMTP_PASSWORD"))
    tencent_smtp_from_email: str = Field(default="noreply@mail.omnedesk.com", validation_alias=AliasChoices("RAYUE_TENCENT_SMTP_FROM_EMAIL", "XPET_TENCENT_SMTP_FROM_EMAIL", "TENCENT_SMTP_FROM_EMAIL"))
    tencent_ses_secret_id: str | None = Field(default=None, validation_alias=AliasChoices("RAYUE_TENCENT_SES_SECRET_ID", "XPET_TENCENT_SES_SECRET_ID", "TENCENTCLOUD_SECRET_ID"))
    tencent_ses_secret_key: str | None = Field(default=None, validation_alias=AliasChoices("RAYUE_TENCENT_SES_SECRET_KEY", "XPET_TENCENT_SES_SECRET_KEY", "TENCENTCLOUD_SECRET_KEY"))
    tencent_ses_region: str = Field(default="ap-hongkong", validation_alias=AliasChoices("RAYUE_TENCENT_SES_REGION", "XPET_TENCENT_SES_REGION"))
    tencent_ses_endpoint: str = Field(default="ses.tencentcloudapi.com", validation_alias=AliasChoices("RAYUE_TENCENT_SES_ENDPOINT", "XPET_TENCENT_SES_ENDPOINT"))
    tencent_ses_from_email: str = Field(default="noreply@mail.omnedesk.com", validation_alias=AliasChoices("RAYUE_TENCENT_SES_FROM_EMAIL", "XPET_TENCENT_SES_FROM_EMAIL", "TENCENT_SES_FROM_EMAIL"))
    tencent_ses_template_id: int | None = Field(default=None, validation_alias=AliasChoices("RAYUE_TENCENT_SES_TEMPLATE_ID", "XPET_TENCENT_SES_TEMPLATE_ID"))
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
