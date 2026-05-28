from app.agent.runtime import AgentRuntime
from app.core.config import get_settings


settings = get_settings()
runtime = AgentRuntime(settings)
