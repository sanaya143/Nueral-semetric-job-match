"""Model prices in USD per 1M tokens: (input, output, cache_read).

Verified against https://openrouter.ai/api/v1/models on 2026-11-05. `:batch` variants
cost 50% (resolves assumption A-002). Unknown models use a deliberately conservative
price so budget checks never under-estimate.
"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float, float]] = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50, 0.01),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00, 0.20),
    "anthropic/claude-opus-5.5": (4.00, 20.00, 0.20),
    "openai/gpt-5-nano": (0.05, 0.40, 0.005),
    "qwen/qwen3.7-flash": (0.03, 0.13, 0.006),
    "deepseek/deepseek-v4.1-flash": (0.045, 0.50, 0.018),
    # embeddings (input only)
    "baai/bge-m3": (0.01, 0.0, 0.0),
    "qwen/qwen3-embedding-8b": (0.01, 0.0, 0.0),
    "openai/text-embedding-3-small": (0.02, 0.0, 0.0),
    "perplexity/pplx-embed-v1-0.6b": (0.004, 0.0, 0.0),
}
CONSERVATIVE = (5.00, 25.00, 0.50)


def price(model: str) -> tuple[float, float, float]:
    if model in PRICES:
        return PRICES[model]
    if model.endswith(":batch") and model[: -len(":batch")] in PRICES:
        i, o, c = PRICES[model[: -len(":batch")]]
        return i / 2, o / 2, c / 2
    return CONSERVATIVE


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    pi, po, pc = price(model)
    fresh = max(0, input_tokens - cached_tokens)
    return (fresh * pi + cached_tokens * pc + output_tokens * po) / 1_000_000


def estimate_tokens(text: str) -> int:
    # Claude 5.x tokenizers count about 30% more tokens than older ones for the same text, so
    # the old 4-characters-per-token rule under-estimated; 3 keeps budget pre-checks conservative.
    return max(1, -(-len(text) // 3))
