"""Application settings.

All settings can be overridden with environment variables or a `.env` file in the
project root, e.g. `NSM_LLM_MODE=openrouter`.

Two run modes:
- offline (default): deterministic heuristics, local concept embeddings, $0 token cost.
- openrouter: LLM calls and embeddings go through OpenRouter via the Model Gateway.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NSM_", env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- storage (local, no Docker) -------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str = ""  # default: sqlite file inside data_dir

    # --- modes -----------------------------------------------------------------------
    llm_mode: str = "offline"  # offline | openrouter
    embedding_mode: str = "local"  # local | openrouter
    rerank_mode: str = "none"  # none | cohere_compatible

    # --- OpenRouter --------------------------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_fee: float = 0.055  # fee on credit purchases, applied to metered cost
    provider_allowlist: str = ""  # comma-separated provider slugs; empty = no allowlist
    use_batch_variants: bool = False  # append ':batch' for bulk ingestion (50% cheaper, slower)

    # Task -> model routing (IDs verified against openrouter.ai/api/v1/models on 2026-11-05)
    model_extract: str = "anthropic/claude-haiku-5.5"
    model_escalate: str = "anthropic/claude-sonnet-5.5"
    model_jd: str = "anthropic/claude-sonnet-5.5"
    model_verify: str = "anthropic/claude-haiku-5.5"
    model_deep: str = "anthropic/claude-sonnet-5.5"
    model_report: str = "anthropic/claude-haiku-5.5"
    embedding_model: str = "baai/bge-m3"  # $0.01 / 1M tokens on OpenRouter

    # Optional Cohere-compatible rerank endpoint (POST {url} with model/query/documents)
    rerank_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""

    # --- cost caps (decisions D-018, D-019) --------------------------------------------
    cap_per_run_usd: float = 0.20
    cap_per_cv_usd: float = 0.003       # average target per CV (reported, alerted on)
    cap_per_cv_hard_usd: float = 0.024  # hard stop for one CV; must fit extract + one Sonnet escalation (D-023)
    cap_monthly_usd: float = 50.0
    alert_fraction: float = 0.8

    # --- funnel sizes (decision D-011) -------------------------------------------------
    stage1_top_k: int = 300
    stage2_top_k: int = 50
    deep_eval_eager_k: int = 5
    deep_eval_lazy_k: int = 10

    # --- local embedding ---------------------------------------------------------------
    local_embedding_dim: int = 512

    default_tenant: str = "demo"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'nsm.db').as_posix()}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_mode == "openrouter" and bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
