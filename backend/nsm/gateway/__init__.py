"""Model Gateway: the only way the application talks to a model (ADR 0003).

Responsibilities
- task -> model routing (config), per-task max_tokens; hidden reasoning switched off
- budget guards: per match run, per CV, per month (decisions D-018/D-019), checked
  *before* each call with a worst-case estimate (input estimate + full max_tokens)
- cost meter: one `llm_calls` row per external call (tokens, cost, latency, outcome)
- a last-line PII scrub (emails / phones / URLs) on every prompt
- embeddings (OpenRouter or the local concept-hash embedder) and optional rerank

Callers catch `GatewayUnavailable` (offline mode, budget exceeded, provider error) and
fall back to the deterministic path, flagging the result. Nothing ever silently fails open.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nsm.config import Settings, get_settings
from nsm.gateway.clients import CohereCompatibleReranker, LLMError, OpenRouterClient, ThinkingRequired, Usage
from nsm.gateway.embeddings import LOCAL_MODEL_NAME, LocalConceptEmbedder
from nsm.gateway.pricing import cost_usd, estimate_tokens
from nsm.ingestion.pii import EMAIL, PHONE, URL
from nsm.models import LlmCall

# Output caps per task (tokens): about 2x the largest output measured by scripts/token_profile.py.
# Billing is on actual tokens; the cap bounds the worst case each budget check reserves.
MAX_TOKENS = {"extract": 2000, "escalate": 800, "jd": 1500, "verify": 700, "deep": 1000, "report": 400,
              "search": 300}
# Hidden reasoning ("thinking") is billed as output tokens. Claude Haiku 5.5 and Sonnet 5.5 think by
# default (effort medium / high) unless told not to, and every task here is bounded, schema-checked
# labelling, so thinking is off for all of them (D-024). None = off; a string sets an effort level.
EFFORT: dict[str, str | None] = {t: None for t in MAX_TOKENS}
THINKING_DEFAULT_OFF = True
# A model that refuses to switch thinking off runs at the lowest effort instead, with this many extra
# output tokens allowed (and reserved by the budget check) so the JSON answer still fits.
FALLBACK_EFFORT, THINKING_RESERVE = "low", 1024
CV_TASKS = {"extract", "escalate"}


class GatewayUnavailable(RuntimeError):
    """LLM path not usable for this call; use the deterministic fallback."""


class BudgetExceeded(GatewayUnavailable):
    pass


def scrub(text: str) -> str:
    text = EMAIL.sub("[email]", text)
    text = URL.sub("[link]", text)
    return PHONE.sub("[phone]", text)


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class Gateway:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._client: OpenRouterClient | None = None
        self._local_embedder: LocalConceptEmbedder | None = None
        self._reranker: CohereCompatibleReranker | None = None
        self._thinking_required: set[str] = set()  # models that refused reasoning off

    # ------------------------------------------------------------------ routing
    @property
    def llm_enabled(self) -> bool:
        return self.s.llm_enabled

    def model_for(self, task: str, bulk: bool = False) -> str:
        model = getattr(self.s, f"model_{task}", None) or self.s.model_extract
        if bulk and self.s.use_batch_variants and not model.endswith(":batch"):
            model += ":batch"
        return model

    @property
    def client(self) -> OpenRouterClient:
        if self._client is None:
            allow = [p.strip() for p in self.s.provider_allowlist.split(",")]
            self._client = OpenRouterClient(self.s.openrouter_api_key, self.s.openrouter_base_url, allow)
        return self._client

    # ------------------------------------------------------------------ budgets
    def spent(self, session: Session, *, run_id: str | None = None, candidate_id: str | None = None,
              month: bool = False) -> float:
        q = select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
        if run_id:
            q = q.where(LlmCall.run_id == run_id)
        if candidate_id:
            q = q.where(LlmCall.candidate_id == candidate_id, LlmCall.task.in_(CV_TASKS))
        if month:
            q = q.where(LlmCall.created_at >= _month_start())
        return float(session.scalar(q) or 0.0)

    def check_budget(self, session: Session, estimate: float, *, task: str, run_id: str | None,
                     candidate_id: str | None) -> None:
        fee = 1 + self.s.openrouter_fee
        est = estimate * fee
        if self.spent(session, month=True) + est > self.s.cap_monthly_usd:
            raise BudgetExceeded(f"monthly cap ${self.s.cap_monthly_usd:.2f} reached")
        if run_id and self.spent(session, run_id=run_id) + est > self.s.cap_per_run_usd:
            raise BudgetExceeded(f"per-run cap ${self.s.cap_per_run_usd:.2f} reached")
        if candidate_id and task in CV_TASKS and \
                self.spent(session, candidate_id=candidate_id) + est > self.s.cap_per_cv_hard_usd:
            # $0.003/CV is an average target; one CV may need extract + a Sonnet escalation (~$0.012 worst
            # case, ~$0.023 if the model refuses thinking-off and needs the reserve).
            raise BudgetExceeded(f"per-CV hard cap ${self.s.cap_per_cv_hard_usd:.3f} reached for {candidate_id}")

    def _record(self, session: Session, tenant_id: str, task: str, model: str, usage: Usage, *,
                run_id: str | None, candidate_id: str | None, ok: bool = True, error: str | None = None) -> float:
        cost = usage.cost_usd if usage.cost_usd is not None else cost_usd(
            model, usage.input_tokens, usage.output_tokens + usage.reasoning_tokens, usage.cached_tokens)
        cost *= 1 + self.s.openrouter_fee
        session.add(LlmCall(tenant_id=tenant_id, task=task, model=model, run_id=run_id, candidate_id=candidate_id,
                            input_tokens=usage.input_tokens, cached_tokens=usage.cached_tokens,
                            output_tokens=usage.output_tokens, reasoning_tokens=usage.reasoning_tokens,
                            cost_usd=round(cost, 8), latency_ms=usage.latency_ms, ok=ok, error=error))
        session.flush()
        return cost

    # ------------------------------------------------------------------ chat
    def chat_json(self, session: Session, *, tenant_id: str, task: str, system: str, user: str, schema: dict,
                  schema_name: str, run_id: str | None = None, candidate_id: str | None = None,
                  bulk: bool = False) -> dict:
        if not self.llm_enabled:
            raise GatewayUnavailable("offline mode")
        model = self.model_for(task, bulk=bulk)
        effort, max_tokens = EFFORT.get(task), MAX_TOKENS.get(task, 1000)
        if effort is None and model in self._thinking_required:
            effort, max_tokens = FALLBACK_EFFORT, max_tokens + THINKING_RESERVE
        user = scrub(user)
        est = cost_usd(model, estimate_tokens(system) + estimate_tokens(user), max_tokens)
        self.check_budget(session, est, task=task, run_id=run_id, candidate_id=candidate_id)
        try:
            data, usage = self.client.chat_json(model, system, user, schema, schema_name, max_tokens, effort)
        except ThinkingRequired:
            # The provider refused "thinking off" for this model (a 400, not billed). Remember it and
            # retry once at the lowest effort; the retry's larger worst case is budget-checked again.
            self._thinking_required.add(model)
            return self.chat_json(session, tenant_id=tenant_id, task=task, system=system, user=user, schema=schema,
                                  schema_name=schema_name, run_id=run_id, candidate_id=candidate_id, bulk=bulk)
        except LLMError as e:
            self._record(session, tenant_id, task, model, Usage(), run_id=run_id, candidate_id=candidate_id,
                         ok=False, error=str(e)[:500])
            raise GatewayUnavailable(str(e)) from e
        except ValueError as e:  # invalid JSON
            self._record(session, tenant_id, task, model, Usage(), run_id=run_id, candidate_id=candidate_id,
                         ok=False, error=f"json: {e}"[:500])
            raise GatewayUnavailable(f"invalid JSON from model: {e}") from e
        self._record(session, tenant_id, task, model, usage, run_id=run_id, candidate_id=candidate_id)
        return data

    # ------------------------------------------------------------------ embeddings
    @property
    def local_embedder(self) -> LocalConceptEmbedder:
        if self._local_embedder is None:
            self._local_embedder = LocalConceptEmbedder(dim=self.s.local_embedding_dim)
        return self._local_embedder

    @property
    def embedding_model(self) -> str:
        if self.s.embedding_mode == "openrouter" and self.s.openrouter_api_key:
            return self.s.embedding_model
        return LOCAL_MODEL_NAME

    def embed(self, session: Session, texts: list[str], *, tenant_id: str, model: str | None = None,
              run_id: str | None = None, candidate_id: str | None = None) -> tuple[np.ndarray, str]:
        """Returns (L2-normalised float32 matrix, model name). Falls back to local on any failure."""
        model = model or self.embedding_model
        if model == LOCAL_MODEL_NAME or not self.s.openrouter_api_key:
            return self.local_embedder.embed(texts), LOCAL_MODEL_NAME
        texts = [scrub(t)[:8000] for t in texts]
        try:
            est = cost_usd(model, sum(estimate_tokens(t) for t in texts), 0)
            self.check_budget(session, est, task="embed", run_id=run_id, candidate_id=None)
            vecs: list[list[float]] = []
            for i in range(0, len(texts), 64):
                chunk, usage = self.client.embed(model, texts[i:i + 64])
                self._record(session, tenant_id, "embed", model, usage, run_id=run_id, candidate_id=candidate_id)
                vecs.extend(chunk)
            arr = np.asarray(vecs, dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return arr / norms, model
        except (GatewayUnavailable, LLMError, KeyError, ValueError):
            return self.local_embedder.embed(texts), LOCAL_MODEL_NAME

    # ------------------------------------------------------------------ rerank
    @property
    def rerank_enabled(self) -> bool:
        return self.s.rerank_mode == "cohere_compatible" and bool(self.s.rerank_url)

    def rerank(self, session: Session, query: str, documents: list[str], *, tenant_id: str, top_n: int,
               run_id: str | None = None) -> list[tuple[int, float]] | None:
        if not self.rerank_enabled or not documents:
            return None
        if self._reranker is None:
            self._reranker = CohereCompatibleReranker(self.s.rerank_url, self.s.rerank_api_key, self.s.rerank_model)
        try:
            res, usage = self._reranker.rerank(scrub(query), [scrub(d) for d in documents], top_n)
            self._record(session, tenant_id, "rerank", self.s.rerank_model or "rerank", usage, run_id=run_id,
                         candidate_id=None)
            return res
        except LLMError:
            return None


_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def reset_gateway() -> None:
    global _gateway
    _gateway = None
