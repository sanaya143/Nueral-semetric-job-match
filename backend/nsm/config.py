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
LARGE_TASKS = {"escalate", "jd", "deep"}
ALL_TASKS = ("extract", "escalate", "jd", "verify", "deep", "report", "search")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NSM_", env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- storage (local, no Docker) -------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    # Default: a SQLite file inside data_dir (local use). For Azure Database for PostgreSQL use e.g.
    # postgresql+psycopg://USER:PASSWORD@SERVER.postgres.database.azure.com:5432/nsm?sslmode=require
    database_url: str = ""
    db_pool_size: int = 5

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

    # Task -> model routing. No model is built in: choose any OpenRouter model IDs in `.env`.
    # Two tiers cover every task; a per-task setting (e.g. NSM_MODEL_DEEP) overrides its tier.
    model_small: str = ""   # extraction, verification, search, report
    model_large: str = ""   # escalation, job parsing, deep evaluation
    model_extract: str = ""
    model_escalate: str = ""
    model_jd: str = ""
    model_verify: str = ""
    model_deep: str = ""
    model_report: str = ""
    model_search: str = ""
    embedding_model: str = ""  # required when NSM_EMBEDDING_MODE=openrouter
    # Prices in USD per 1M tokens for the models above, as JSON: {"vendor/model": [input, output, cache_read]}.
    # Models without a price use a deliberately high estimate, so budget checks never under-estimate.
    model_prices: str = ""
    # Send provider prompt-cache hints on the system prompt (only for providers that need explicit hints).
    explicit_cache_control: bool = False

    # Optional Cohere-compatible rerank endpoint (POST {url} with model/query/documents)
    rerank_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""

    # --- cost caps (decisions D-018, D-019) --------------------------------------------
    cap_per_run_usd: float = 0.20
    cap_per_cv_usd: float = 0.003       # average target per CV (reported, alerted on)
    cap_per_cv_hard_usd: float = 0.024  # hard stop for one CV; must fit extract + one large-model escalation (D-023)
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

    # --- OCR for scanned PDFs and image CVs (see nsm/ingestion/ocr.py) ------------------
    ocr_engine: str = "auto"  # auto | local | azure | off
    ocr_max_pages: int = 10
    ocr_timeout_seconds: int = 60
    azure_docintel_endpoint: str = ""  # https://<resource>.cognitiveservices.azure.com
    azure_docintel_key: str = ""
    azure_docintel_api_version: str = "2024-11-30"

    # --- who is calling, and which workspace they may use (see nsm/security.py) ---------
    # none:    local single-user build; the X-Tenant-ID header picks the workspace (never expose this mode)
    # api_key: each key belongs to one workspace; NSM_API_KEYS = {"<sha256 of key>": "<workspace>"}
    # entra:   Microsoft Entra ID (or any OIDC) bearer token; the workspace comes from a token claim
    auth_mode: str = "none"
    api_keys: str = ""
    oidc_issuer: str = ""        # e.g. https://login.microsoftonline.com/<directory-id>/v2.0
    oidc_audience: str = ""      # the API's application (client) ID or App ID URI
    oidc_jwks_url: str = ""      # default: <issuer>/discovery/v2.0/keys for Entra, else from discovery
    oidc_tenant_claim: str = "tid"
    oidc_tenant_map: str = ""    # optional JSON {"<claim value>": "<workspace>"}; unmapped values are refused

    # --- abuse limits ----------------------------------------------------------------------
    rate_limit_per_minute: int = 300        # all API requests, per workspace (or per IP before login)
    write_rate_limit_per_minute: int = 60   # POST/PUT/PATCH/DELETE, per workspace
    daily_upload_quota: int = 2000          # CV files per workspace per UTC day (0 = no quota)
    daily_match_run_quota: int = 200        # match runs per workspace per UTC day (0 = no quota)

    # --- local-only HTTP guard (no login in this build) ----------------------------------
    # Host names the API answers to. Blocks DNS-rebinding: a web page that points its own
    # domain at 127.0.0.1 sends its own Host header and is refused.
    allowed_hosts: str = "127.0.0.1,localhost"
    # Browser origins allowed to send POST/PUT/PATCH/DELETE (besides the API's own origin).
    # Blocks other websites from uploading CVs or changing data through your browser (CSRF).
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'nsm.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    def model_for_task(self, task: str) -> str:
        tier = self.model_large if task in LARGE_TASKS else self.model_small
        return getattr(self, f"model_{task}", "") or tier

    @property
    def missing_models(self) -> list[str]:
        """Tasks that have no model configured (LLM mode stays off until they all do)."""
        return [t for t in ALL_TASKS if not self.model_for_task(t)]

    @property
    def llm_enabled(self) -> bool:
        return self.llm_mode == "openrouter" and bool(self.openrouter_api_key) and not self.missing_models


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
