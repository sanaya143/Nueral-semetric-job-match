"""Model prices in USD per 1M tokens: (input, output, cache_read).

No vendor prices are built in. Set them for the models you configure with NSM_MODEL_PRICES
(JSON: {"vendor/model": [input, output, cache_read]}). `:batch` variants cost 50%.
Unknown models use a deliberately conservative price so budget checks never under-estimate.
When the provider reports the real cost of a call, the cost meter records that instead.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("nsm.gateway.pricing")
CONSERVATIVE = (5.00, 25.00, 0.50)


def _table() -> dict[str, tuple[float, float, float]]:
    from nsm.config import get_settings

    raw = get_settings().model_prices.strip()
    if not raw:
        return {}
    try:
        return {k: tuple(float(x) for x in v)[:3] for k, v in json.loads(raw).items()}  # type: ignore[misc]
    except (ValueError, TypeError, AttributeError):
        log.warning("NSM_MODEL_PRICES is not valid JSON; using conservative prices")
        return {}


def price(model: str) -> tuple[float, float, float]:
    prices = _table()
    if model in prices:
        return prices[model]
    if model.endswith(":batch") and model[: -len(":batch")] in prices:
        i, o, c = prices[model[: -len(":batch")]]
        return i / 2, o / 2, c / 2
    return CONSERVATIVE


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    pi, po, pc = price(model)
    fresh = max(0, input_tokens - cached_tokens)
    return (fresh * pi + cached_tokens * pc + output_tokens * po) / 1_000_000


def estimate_tokens(text: str) -> int:
    # Newer tokenizers can count about 30% more tokens than older ones for the same text, so the
    # old 4-characters-per-token rule under-estimates; 3 keeps budget pre-checks conservative.
    return max(1, -(-len(text) // 3))
