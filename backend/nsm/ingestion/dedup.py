"""Duplicate detection before any LLM spend (exact content hash on normalised text).

Near-duplicate detection (MinHash) and identity resolution are planned for the
production phase.
"""

from __future__ import annotations

import hashlib
import re


def content_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()
