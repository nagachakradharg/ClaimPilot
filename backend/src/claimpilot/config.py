"""Application settings. Everything tunable lives here and is set via environment / `.env`.

Switching LLM tier (fake -> replay -> local -> paid) must be an `.env` change only.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = REPO_DIR / "data"
EVALS_DIR = REPO_DIR / "evals"
EMBEDDING_DIM = 384  # fixed by the DB schema; must match the embedding model


class LLMMode(StrEnum):
    FAKE = "fake"  # canned responses, no network (tests, CI, UI work)
    REPLAY = "replay"  # serve previously recorded real responses; miss = error
    RECORD = "record"  # call the real model and save the response for replay
    LIVE = "live"  # call the real model, don't save


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"

    database_url: str = "postgresql+psycopg://claimpilot:claimpilot@localhost:5432/claimpilot"

    llm_mode: LLMMode = LLMMode.FAKE
    # "provider:model", e.g. "ollama:gpt-oss:20b", "openai:<model>", "anthropic:<model>"
    llm_default_model: str = "ollama:gemma4:12b"  # text + vision; see evals/report.md
    # Per-node overrides as JSON, e.g. {"intake": "openai:<vision-model>"}
    llm_node_models: dict[str, str] = Field(default_factory=dict)
    llm_temperature: float = 0.0
    # Ollama only: thinking control for reasoning models. Unset = model default;
    # "false" turns thinking off; "low" | "medium" | "high" set effort where supported (gpt-oss).
    llm_reasoning: str | None = "false"
    llm_num_predict: int = 2048  # Ollama max output tokens: a runaway generation fails fast
    llm_num_ctx: int = 8192  # Ollama context window (its default of 4096 is tight with photos)
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    llm_fixtures_dir: Path = BACKEND_DIR / "fixtures" / "llm"

    # "fastembed" = local ONNX model (no key); "hash" = deterministic fake for tests
    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    retrieval_top_k: int = 6

    fraud_review_threshold: float = 0.6
    payout_review_threshold: float = 5000.0
    extraction_confidence_min: float = 0.7

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    # "inprocess" runs the service inside this Python process (dev, tests, CLI);
    # otherwise a URL, e.g. http://core:8100 and http://mcp:8200/mcp in Docker Compose.
    core_system_url: str = "inprocess"
    mcp_server_url: str = "inprocess"
    tool_timeout_s: float = 20.0
    tool_max_retries: int = 2

    max_photos: int = 6
    max_photo_bytes: int = 10 * 1024 * 1024
    photo_max_side_px: int = 1024
    photo_storage_dir: Path = REPO_DIR / "storage" / "photos"
    total_loss_threshold: float = 0.75

    # Dev default only; set a long random JWT_SECRET in any shared or cloud environment.
    jwt_secret: str = "dev-only-change-me-0123456789abcdef0123456789abcdef"
    jwt_ttl_minutes: int = 120

    # USD per 1M tokens per "provider:model", e.g. {"openai:<model>": [0.15, 0.60]}.
    # Unlisted models (incl. local Ollama) cost 0.
    model_prices: dict[str, list[float]] = Field(default_factory=dict)

    manual_handling_minutes: float = 45.0  # stated assumption, shown on the dashboard

    cors_origins: list[str] = ["http://localhost:5173"]

    def model_for(self, node: str) -> str:
        """Return the "provider:model" spec configured for a graph node."""
        return self.llm_node_models.get(node, self.llm_default_model)

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
