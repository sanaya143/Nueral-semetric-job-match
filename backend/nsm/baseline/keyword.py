"""Keyword-ATS baseline for evaluation (what we claim to beat).

Mimics a typical keyword screen: extract keywords from the job (title + requirement text),
count how often each appears anywhere in the CV (title, skills list, summary included),
cap repeats, add a title-match bonus. No evidence levels, no synonyms beyond exact terms.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z][a-z0-9+#.]*")
_STOP = set("""a an and or the of to in on for with at by from as is are be will you we our your this that these those
have has had experience years year strong good excellent knowledge ability able work working skills skill using use
plus nice must required preferred etc including such like team teams role who what which job location hybrid remote
onsite days notice period degree bachelor master least minimum candidates candidate looking """.split())


def keywords(title: str, requirement_texts: list[str]) -> list[str]:
    seen: list[str] = []
    for text in [title, *requirement_texts]:
        for tok in _TOKEN.findall(text.lower()):
            tok = tok.strip(".")
            if len(tok) >= 2 and tok not in _STOP and tok not in seen:
                seen.append(tok)
    return seen


def score(cv_text: str, title: str, requirement_texts: list[str]) -> float:
    text = cv_text.lower()
    tokens = _TOKEN.findall(text)
    counts: dict[str, int] = {}
    for t in tokens:
        t = t.strip(".")
        counts[t] = counts.get(t, 0) + 1
    kws = keywords(title, requirement_texts)
    if not kws:
        return 0.0
    hit = sum(min(counts.get(k, 0), 3) for k in kws) / (3 * len(kws))
    title_bonus = 0.2 if title and title.lower() in text else 0.0
    return round(100 * min(1.0, hit + title_bonus), 2)
