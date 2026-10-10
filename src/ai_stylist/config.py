from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+asyncpg://stylist:stylist@localhost:5432/stylist"
    openrouter_api_key: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    intent_model: str = ""
    enrichment_model: str = ""
    stylist_model: str = ""
    response_model: str = ""
    intent_reasoning_effort: ReasoningEffort = "medium"
    enrichment_reasoning_effort: ReasoningEffort = "low"
    stylist_reasoning_effort: ReasoningEffort = "high"
    response_reasoning_effort: ReasoningEffort = "none"
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=1536, ge=1)
    max_candidates: int = Field(default=30, ge=3, le=30)
    request_timeout_seconds: float = Field(default=120, gt=0)
    image_max_bytes: int = Field(default=10_000_000, ge=1)
    offline_concurrency: int = Field(default=2, ge=1, le=10)
    data_dir: Path = Path("data")
    knowledge_dir: Path = Path("knowledge")

    def require(self, *fields: str) -> None:
        missing = [name.upper() for name in fields if not getattr(self, name)]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        for name in fields:
            if name.endswith("_model"):
                model_id = getattr(self, name)
                if model_id.startswith("openrouter:") or "/" not in model_id:
                    raise ValueError(f"{name.upper()} must be an OpenRouter provider/model ID")
