import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    demo_access_token: str = ""
    agent_tool_key: str = ""
    session_signing_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_webhook_secret: str = ""
    agent_id: str = ""
    vision_api_key: str = ""
    vision_provider: str = "openai"
    google_api_key: str = ""
    google_vision_model: str = "gemini-3-flash-preview"
    vision_model: str = "gpt-5.5"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-5.5"
    airtable_token: str = ""
    airtable_base_id: str = ""
    airtable_table_name: str = "LumaField Actions"
    session_ttl_seconds: int = 3600

    @classmethod
    def from_env(cls):
        def env(key, default=""):
            return os.environ.get("LUMAFIELD_" + key, default)
        return cls(
            data_dir=Path(env("DATA_DIR", "./data")),
            demo_access_token=env("DEMO_ACCESS_TOKEN"),
            agent_tool_key=env("AGENT_TOOL_KEY"),
            session_signing_key=env("SESSION_SIGNING_KEY"),
            elevenlabs_api_key=env("ELEVENLABS_API_KEY"),
            elevenlabs_webhook_secret=env("ELEVENLABS_WEBHOOK_SECRET"),
            agent_id=env("AGENT_ID"), vision_api_key=env("VISION_API_KEY"),
            vision_provider=env("VISION_PROVIDER", "openai"),
            google_api_key=env("GOOGLE_API_KEY"),
            google_vision_model=env("GOOGLE_VISION_MODEL", "gemini-3-flash-preview"),
            vision_model=env("VISION_MODEL", "gpt-5.5"),
            openrouter_api_key=env("OPENROUTER_API_KEY"),
            openrouter_model=env("OPENROUTER_MODEL", "openai/gpt-5.5"),
            airtable_token=env("AIRTABLE_TOKEN"), airtable_base_id=env("AIRTABLE_BASE_ID"),
            airtable_table_name=env("AIRTABLE_TABLE_NAME", "LumaField Actions"),
        )
