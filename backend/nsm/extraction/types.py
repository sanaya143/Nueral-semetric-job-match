from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UnitDraft:
    key: str
    kind: str  # experience|project|achievement|summary|skills|certifications|education|preferences|other
    title: str
    context_header: str
    start: int
    end: int
    text: str
    start_year: float | None = None
    end_year: float | None = None
    header_len: int = 0  # characters of the header line(s): titles are context, not evidence


@dataclass
class ClaimDraft:
    unit_key: str
    skill_id: str
    level: str
    confidence: float
    quote: str
    via: str = "direct"


@dataclass
class ExtractionResult:
    units: list[UnitDraft]
    claims: list[ClaimDraft]
    headline: str = ""
    location: str | None = None
    work_mode: str | None = None
    notice_period_days: int | None = None
    total_years: float = 0.0
    flags: list[str] = field(default_factory=list)
    mode: str = "heuristic"
    unmapped_terms: list[str] = field(default_factory=list)
