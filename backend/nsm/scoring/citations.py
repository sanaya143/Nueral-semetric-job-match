"""Citation validator (anti-hallucination rule 2, ADR 0006).

A quote is accepted only if it is an exact substring of the cited evidence unit, after
collapsing whitespace (PDF extraction can change line breaks); a case-insensitive match is
the only other tolerance. Paraphrases, merged fragments and invented text are rejected
and the verdict falls back to the deterministic one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WS = re.compile(r"\s+")
MIN_QUOTE_CHARS = 12


@dataclass
class QuoteCheck:
    ok: bool
    start: int = -1
    end: int = -1
    reason: str = ""


def _collapse(s: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space, keeping a map back to original offsets."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(s):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def validate_quote(quote: str | None, source: str, *, body_from: int = 0) -> QuoteCheck:
    """`body_from`: quotes that lie entirely before this offset (e.g. a job-title header) are
    rejected: titles are context, never evidence."""
    if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
        return QuoteCheck(False, reason="quote_too_short")
    q = _WS.sub(" ", quote.strip().strip('"').strip("“”").strip())
    pos = source.find(q)
    if pos >= 0:
        start, end = pos, pos + len(q)
    else:
        coll, idx = _collapse(source)
        p2 = coll.find(q)
        if p2 < 0:
            p2 = coll.lower().find(q.lower())
            if p2 < 0:
                return QuoteCheck(False, reason="not_in_source")
        start, end = idx[p2], idx[p2 + len(q) - 1] + 1
    if end <= body_from:
        return QuoteCheck(False, start, end, reason="quote_is_title_or_header")
    return QuoteCheck(True, start, end)
