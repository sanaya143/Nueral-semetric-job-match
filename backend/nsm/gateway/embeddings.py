"""Local, deterministic "concept-hash" embeddings for offline mode.

Not a neural model. Each text becomes a signed feature-hashed vector built from:
  - canonical taxonomy concepts found in the text (+ their is-a ancestors), weighted high
  - content words and bigrams (light stemming), weighted low
So "document retrieval pipeline" and "RAG system" land close together because both
map to the `rag` concept. Good enough for an offline funnel; production uses API or
self-hosted neural embeddings (D-016).
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

from nsm.taxonomy import Taxonomy, get_taxonomy

LOCAL_MODEL_NAME = "local-concept-hash-v1"

_WORD = re.compile(r"[a-z][a-z0-9+#.-]{2,}")
_STOP = set("""the and for with from that this into over under using use used was were are has have had will
would can could our their its his her they them you your about across within without via per each such also
than then when where which while who whom what more most less very team teams work worked working
responsible various""".split())


def _stem(w: str) -> str:
    for suf in ("ings", "ing", "ers", "ies", "ied", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _bucket(key: str, dim: int) -> tuple[int, float]:
    h = hashlib.md5(key.encode("utf-8")).digest()
    idx = int.from_bytes(h[:4], "little") % dim
    sign = 1.0 if h[4] & 1 else -1.0
    return idx, sign


class LocalConceptEmbedder:
    model_name = LOCAL_MODEL_NAME

    def __init__(self, dim: int = 512, taxonomy: Taxonomy | None = None):
        self.dim = dim
        self.tax = taxonomy or get_taxonomy()

    def embed_one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)

        def add(key: str, w: float) -> None:
            i, s = _bucket(key, self.dim)
            v[i] += s * w

        for m in self.tax.find(text):
            add(f"skill:{m.skill_id}", 3.0)
            for anc in self.tax.ancestors(m.skill_id):
                add(f"skill:{anc}", 1.5)
        words = [_stem(w.strip(".-")) for w in _WORD.findall(text.lower())]
        words = [w for w in words if w not in _STOP]
        for w in words:
            add(f"w:{w}", 1.0)
        for a, b in zip(words, words[1:]):
            add(f"b:{a}_{b}", 0.5)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self.embed_one(t) for t in texts])
