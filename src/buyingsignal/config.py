"""Application configuration.

All configuration — including secrets — is loaded from the environment only.
Nothing is read from arbitrary files at runtime except an optional local `.env`
for development. Secret values must never be written to logs (see `logging.py`,
which installs a redacting filter).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LLMProvider(str, Enum):
    groq = "groq"
    deepinfra = "deepinfra"
    openai_compatible = "openai-compatible"


class RestSource(BaseModel):
    """Declarative description of a generic permitted JSON REST source.

    The collector fetches `url`, walks `items_path` (dot path) to a list, and
    maps each item's fields into a RawSignal. Keeps adding sources to config,
    not code.
    """

    name: str
    url: str
    items_path: str = ""  # dot path to the list of records; "" means top-level list
    uid_field: str = "id"
    title_field: str = "title"
    url_field: str = "url"
    text_field: str = ""  # optional richer body field
    headers: dict[str, str] = Field(default_factory=dict)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM ---------------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.groq
    llm_model: str = "llama-3.3-70b-versatile"
    llm_base_url: str = ""
    # Groq uses its own key var; other providers use LLM_API_KEY.
    groq_api_key: str = ""
    llm_api_key: str = ""

    # --- Datastores --------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://buyingsignal:buyingsignal@localhost:5432/buyingsignal"
    )
    redis_url: str = "redis://localhost:6379/0"

    # --- Slack -------------------------------------------------------------
    slack_webhook_url: str = ""

    # --- Collectors --------------------------------------------------------
    edgar_user_agent: str = "buyingsignal/0.1 (ops@example.com)"
    # NoDecode: keep the raw env string so the comma-splitting validator runs
    # instead of pydantic-settings trying (and failing) to JSON-decode it.
    rss_feeds: Annotated[list[str], NoDecode] = Field(default_factory=list)
    rest_sources: list[RestSource] = Field(default_factory=list)

    edgar_interval_seconds: int = 900
    rss_interval_seconds: int = 600
    rest_interval_seconds: int = 900

    # --- Scoring -----------------------------------------------------------
    score_threshold: int = 60

    # --- Ops ---------------------------------------------------------------
    log_level: str = "INFO"
    debug_log_payloads: bool = False

    @field_validator("rss_feeds", mode="before")
    @classmethod
    def _split_feeds(cls, v: object) -> object:
        # Accept comma-separated string from env as well as a real list.
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def active_llm_key(self) -> str:
        """The credential for the configured provider."""
        if self.llm_provider is LLMProvider.groq:
            return self.groq_api_key
        return self.llm_api_key

    @property
    def sync_database_url(self) -> str:
        """psycopg/sync form used by Alembic migrations."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
