# ADR 0003: Internal Model Gateway, with OpenRouter as one backend

- **Status:** Accepted
- **Date:** 2026-11-05
- **Phase:** 1

## Context
The brief's preferred LLM gateway is OpenRouter. The system needs different models for different tasks: extraction, JD understanding, verification, deep evaluation, and reports. It also needs fallbacks, cost control and PII protection.

What we know about OpenRouter:
- **Pricing:** inference is passed through at provider prices, but OpenRouter charges a **5.5% fee on credit purchases**.
- **Data handling:** it adds a sub-processor. Data collection is *allowed* by default. ZDR routing and provider allowlists are available.
- **Batch APIs:** support for providers' batch endpoints (≈50% cheaper) is **unconfirmed**.

Embeddings and reranking are high-volume and latency-sensitive, and they rarely change.

## Decision
Build an internal **Model Gateway** module. It owns:
- task → model routing (config-driven)
- per-task fallback chains across provider families
- retries and timeouts
- per-tenant and per-job budgets
- cost metering on every call
- response caching
- **PII redaction before any external call**

Backends:
1. **OpenRouter:** the default for LLM generation. Every request sets ZDR + `data_collection: deny` + a provider allowlist.
2. **Direct provider APIs:** for bulk batch ingestion, and wherever a direct DPA or data residency is required.
3. **Embeddings and reranking:**
   - **MVP (no GPU, D-016):** API providers called through the gateway, with redacted text only, a DPA, and no-retention terms. The embedding price must be ≤ $0.05/M tokens to keep headroom under the $0.003-per-CV cap (D-018).
   - **Later:** self-hosted inference (BGE-M3 / Qwen3-Embedding; bge-reranker-v2-m3 / Qwen3-Reranker) once volume or data residency justifies it.
   - The model is selected by the Phase 2 bake-off. The embedding model and version are stored with every vector so that a later switch is a planned re-embed.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| Call OpenRouter directly from business code | Model choice gets hard-coded throughout the codebase, with no central budget or PII control and no batch path |
| Single direct provider only | Locks us to one vendor, with no cross-provider fallback |
| LiteLLM proxy as the gateway | A viable option. We may use the LiteLLM library *inside* the gateway, but routing policy, budgets and redaction stay in our own code |

## Consequences
- Swapping a model is a config change, gated by the evaluation set.
- The gateway becomes the one place to audit what data left the system.
- We carry a small abstraction layer, and the provider SDKs differ in ways it has to handle.

## Revisit when
- OpenRouter's data terms, fees or batch support change, or
- a customer requires a single named LLM vendor or in-region inference.
