"""Sanitization: normalise text and neutralise prompt-injection attempts.

CV text is untrusted data. Lines that try to instruct an AI system are removed from the
evaluable text and the candidate is flagged for recruiter review (never auto-rejected).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_INJECTION = re.compile(
    r"(ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?)"
    r"|disregard (all |any )?(the )?(previous|prior|above) (instructions|prompts?)"
    r"|(system|developer) prompt"
    r"|you are (now )?(an?|the) (ai|assistant|language model|recruiter bot)"
    r"|rank (me|this (candidate|profile|cv|resume)) (as )?(the )?(first|highest|top)"
    r"|rate this (profile|candidate|cv|resume) (10|ten|100)"
    r"|(note|message|instruction)s? to (the )?(ai|llm|gpt|screening|ats)"
    r"|<\|im_start\|>|\[/?inst\]|###\s*instruction)",
    re.IGNORECASE,
)

_BULLETS = re.compile(r"^[ \t]*[•●▪◦■►‣∙·*–—]\s*", re.MULTILINE)


@dataclass
class SanitizeResult:
    text: str
    flags: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = text.replace("‑", "-").replace(" ", " ")
    text = _BULLETS.sub("- ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def sanitize(text: str, hidden_snippets: list[str] | None = None) -> SanitizeResult:
    flags: list[str] = []
    removed: list[str] = []
    if hidden_snippets:
        flags.append("hidden_text_removed")
        removed.extend(hidden_snippets)
        if any(_INJECTION.search(h) for h in hidden_snippets):
            flags.append("possible_prompt_injection")
    kept: list[str] = []
    for line in normalize_text(text).split("\n"):
        if _INJECTION.search(line):
            removed.append(line.strip())
            if "possible_prompt_injection" not in flags:
                flags.append("possible_prompt_injection")
            continue
        kept.append(line)
    return SanitizeResult(text="\n".join(kept), flags=flags, removed=removed)
