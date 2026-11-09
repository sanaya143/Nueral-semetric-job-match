"""HTTP clients for external model providers (OpenRouter, Cohere-compatible rerank).

Every OpenRouter request:
- asks for zero-data-retention endpoints and denies data collection (ADR 0003)
- optionally restricts to a provider allowlist
- requires providers that support the requested parameters (structured outputs)
- asks for usage accounting so the cost meter records real cost
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class ThinkingRequired(LLMError):
    """The provider rejected "reasoning off" for this model (HTTP 400, not billed)."""


def _rejects_thinking_off(message: str) -> bool:
    return "HTTP 400" in message and re.search(r"thinking|reasoning", message, re.I) is not None


@dataclass
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None  # provider-reported cost, if any
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_json(content: str) -> dict:
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            raise LLMError("model did not return JSON") from None
        return json.loads(m.group(0))


class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str, provider_allowlist: list[str] | None = None,
                 timeout: float = 90.0, retries: int = 2):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.allowlist = [p for p in (provider_allowlist or []) if p]
        self.timeout = timeout
        self.retries = retries

    def _provider_prefs(self) -> dict:
        prefs: dict[str, Any] = {"data_collection": "deny", "zdr": True, "require_parameters": True}
        if self.allowlist:
            prefs["only"] = self.allowlist
        return prefs

    def _post(self, path: str, body: dict) -> tuple[dict, int]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "X-Title": "Neural-Semantic Matching (local)"}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            t0 = time.perf_counter()
            try:
                r = httpx.post(f"{self.base_url}{path}", json=body, headers=headers, timeout=self.timeout)
                latency = int((time.perf_counter() - t0) * 1000)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
                return r.json(), latency
            except httpx.HTTPError as e:
                last = e
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
        raise LLMError(f"OpenRouter request failed: {last}")

    def chat_json(self, model: str, system: str, user: str, schema: dict, schema_name: str,
                  max_tokens: int, effort: str | None) -> tuple[dict, Usage]:
        sys_content: Any = system
        if model.startswith("anthropic/"):
            # Stable prefix (instructions + taxonomy/rubric) marked cacheable. Prompts below the
            # provider's minimum cacheable length are simply processed uncached.
            sys_content = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": sys_content}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            "provider": self._provider_prefs(),
            "usage": {"include": True},
        }
        # Claude Haiku 5.5 / Sonnet 5.5 think by default when nothing is sent, billed as output tokens,
        # so "off" is always explicit. Claude rejects effort "none"; `enabled: false` maps to thinking off.
        body["reasoning"] = {"effort": effort, "exclude": True} if effort else {"enabled": False}
        try:
            data, latency = self._post("/chat/completions", body)
        except LLMError as e:
            if not effort and _rejects_thinking_off(str(e)):
                raise ThinkingRequired(str(e)) from e
            raise
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape: {str(data)[:200]}") from e
        u = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(u.get("prompt_tokens") or 0),
            cached_tokens=int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            output_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
            cost_usd=float(u["cost"]) if u.get("cost") is not None else None,
            latency_ms=latency, raw=u,
        )
        return _parse_json(content), usage

    def embed(self, model: str, texts: list[str]) -> tuple[list[list[float]], Usage]:
        body = {"model": model, "input": texts, "provider": self._provider_prefs()}
        data, latency = self._post("/embeddings", body)
        rows = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        u = data.get("usage") or {}
        usage = Usage(input_tokens=int(u.get("prompt_tokens") or u.get("total_tokens") or 0),
                      cost_usd=float(u["cost"]) if u.get("cost") is not None else None, latency_ms=latency, raw=u)
        return [r["embedding"] for r in rows], usage


class CohereCompatibleReranker:
    """POST {url} {model, query, documents, top_n} -> {results: [{index, relevance_score}]}"""

    def __init__(self, url: str, api_key: str, model: str, timeout: float = 30.0):
        self.url, self.api_key, self.model, self.timeout = url, api_key, model, timeout

    def rerank(self, query: str, documents: list[str], top_n: int) -> tuple[list[tuple[int, float]], Usage]:
        t0 = time.perf_counter()
        r = httpx.post(self.url, json={"model": self.model, "query": query, "documents": documents, "top_n": top_n},
                       headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        if r.status_code >= 400:
            raise LLMError(f"rerank HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        res = [(int(x["index"]), float(x.get("relevance_score", x.get("score", 0.0)))) for x in data.get("results", [])]
        return res, Usage(latency_ms=int((time.perf_counter() - t0) * 1000), raw=data.get("meta") or {})
