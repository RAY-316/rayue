import tomli_w

from app.core.config import Settings


def render_codex_config(settings: Settings) -> str:
    data = {
        "model": settings.model_name,
        "model_provider": settings.model_provider_id,
        "approval_policy": "never",
        "sandbox_mode": "danger-full-access",
        "web_search": settings.codex_web_search,
        "model_providers": {
            settings.model_provider_id: {
                "name": "Custom OpenAI Compatible",
                "base_url": settings.model_base_url,
                "env_key": "CODEX_API_KEY",
                "wire_api": "responses",
                "requires_openai_auth": False,
            }
        },
        "analytics": {"enabled": False},
    }
    return tomli_w.dumps(data)
