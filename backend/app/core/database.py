from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from app.core.config import get_settings
from app.domain.models import Base


settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id varchar(64) REFERENCES users(id) ON DELETE CASCADE"))
        await conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS workspace_id varchar(64) REFERENCES workspaces(id) ON DELETE SET NULL"))
        await conn.execute(text("ALTER TABLE artifact_records ADD COLUMN IF NOT EXISTS workspace_id varchar(64) REFERENCES workspaces(id) ON DELETE SET NULL"))
        await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS mentioned_files jsonb DEFAULT '[]'::jsonb"))
        await conn.execute(text("ALTER TABLE email_rate_events ADD COLUMN IF NOT EXISTS source_hash varchar(128)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_lower_email ON users (lower(email))"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_sessions_user ON user_sessions (user_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_email_codes_email_expires ON email_codes (email, expires_at DESC)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_email_rate_events_lookup ON email_rate_events (email_hash, sent_at DESC)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_email_rate_events_source_lookup ON email_rate_events (source_hash, sent_at DESC)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_captcha_challenges_source_created ON captcha_challenges (source_hash, created_at DESC)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_captcha_challenges_expires ON captcha_challenges (expires_at)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workspaces_user_created ON workspaces (user_id, created_at)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_user_updated ON conversations (user_id, updated_at DESC)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_workspace_updated ON conversations (workspace_id, updated_at DESC)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_artifact_records_workspace_path ON artifact_records (workspace_id, relative_path)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_workspace_files_workspace_path ON workspace_files (workspace_id, relative_path)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workspace_files_workspace_kind ON workspace_files (workspace_id, kind)"))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
