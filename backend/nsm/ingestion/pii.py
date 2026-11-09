"""PII separation and blind-evaluation redaction.

Contact details go to the PII vault (`candidate_contacts`). Protected or proxy attributes
that are common in Indian CVs (date of birth, age, gender, marital status, religion, caste,
category, nationality, parents' names, photo references, ID numbers) are removed from the
evaluable text so they can never reach an LLM or the scorer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?(?:\(?0?\d{2,4}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,6}(?!\d)")
URL = re.compile(r"(?:https?://|www\.)\S+|(?:linkedin\.com|github\.com|gitlab\.com)/\S+", re.IGNORECASE)

PERSONAL_LABELS = re.compile(
    r"^\s*[-*]?\s*(date of birth|d\.?o\.?b\.?|age|gender|sex|marital status|religion|caste|category|community|"
    r"nationality|father'?s name|mother'?s name|husband'?s name|wife'?s name|spouse|passport( no\.?| number)?|"
    r"pan( no\.?| number)?|aadhaa?r( no\.?| number)?|blood group|permanent address|current address|address|"
    r"photo|photograph|height|weight)\s*[:\-]",
    re.IGNORECASE,
)
PERSONAL_HEADINGS = re.compile(r"^\s*(personal (details|information|profile|data)|declaration)\s*:?\s*$", re.IGNORECASE)
SECTION_HEADING = re.compile(r"^\s*[A-Z][A-Za-z &/]{2,40}:?\s*$")
HONORIFIC = re.compile(r"\b(Mr|Mrs|Ms|Miss|Shri|Smt|Kumari)\.?\s+", re.IGNORECASE)

_NAME_LINE = re.compile(r"^[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,3}$")
_NOT_NAME = re.compile(r"(resume|curriculum|vitae|engineer|developer|analyst|manager|summary|profile|experience)",
                       re.IGNORECASE)


@dataclass
class RedactionResult:
    text: str
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    urls: list[str] = field(default_factory=list)
    redacted_fields: list[str] = field(default_factory=list)


def redact(text: str) -> RedactionResult:
    res = RedactionResult(text="")
    lines = text.split("\n")
    out: list[str] = []
    in_personal = False
    name_checked = False

    for line in lines:
        stripped = line.strip()
        # Candidate name: first non-empty line that looks like a person's name.
        if not name_checked and stripped:
            name_checked = True
            candidate = re.sub(r"^(name\s*:\s*)", "", stripped, flags=re.IGNORECASE)
            if _NAME_LINE.match(candidate) and not _NOT_NAME.search(candidate):
                res.full_name = candidate
                res.redacted_fields.append("name")
                continue
        if re.match(r"^\s*name\s*:", stripped, re.IGNORECASE):
            res.full_name = res.full_name or stripped.split(":", 1)[1].strip()
            res.redacted_fields.append("name")
            continue
        if PERSONAL_HEADINGS.match(stripped):
            in_personal = True
            res.redacted_fields.append("personal_details_section")
            continue
        if in_personal:
            if stripped and SECTION_HEADING.match(stripped) and not PERSONAL_LABELS.match(stripped):
                in_personal = False  # next real section starts
            else:
                continue
        m = PERSONAL_LABELS.match(stripped)
        if m:
            res.redacted_fields.append(m.group(1).lower())
            continue
        # Contact details anywhere in the line
        for e in EMAIL.findall(line):
            res.email = res.email or e
            res.redacted_fields.append("email")
        line = EMAIL.sub("[email]", line)
        for u in URL.findall(line):
            res.urls.append(u)
            res.redacted_fields.append("url")
        line = URL.sub("[link]", line)
        for p in PHONE.findall(line):
            digits = re.sub(r"\D", "", p)
            if len(digits) >= 10:
                res.phone = res.phone or p.strip()
                res.redacted_fields.append("phone")
                line = line.replace(p, "[phone]")
        line = HONORIFIC.sub("", line)
        out.append(line)

    # drop now-empty contact lines like "[email] | [phone] | [link]"
    cleaned = [ln for ln in out if re.sub(r"[\[\]|,•\-\s]|email|phone|link", "", ln) or not ln.strip()]
    res.text = "\n".join(cleaned).strip() + "\n"
    res.redacted_fields = sorted(set(res.redacted_fields))
    return res
