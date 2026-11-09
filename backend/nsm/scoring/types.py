from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from nsm.models import Candidate, EvidenceUnit, RequirementAtom, SkillClaim
from nsm.taxonomy import LEVEL_RANK

LEVEL_SCORE = {"demonstrated": 1.0, "applied": 0.7, "inferred": 0.4, "listed_only": 0.2, "missing": 0.0}
STATUS_SCORE = {"met": 1.0, "partial": 0.5, "unknown": 0.5, "not_met": 0.0}


@dataclass
class AtomSpec:
    id: str
    text: str
    kind: str = "must"
    dimension: str = "technical"
    skill_ids: list[str] = field(default_factory=list)
    logic: str = "all"
    min_years: float | None = None
    weight: float = 1.0

    @classmethod
    def from_row(cls, a: RequirementAtom) -> "AtomSpec":
        return cls(a.id, a.text, a.kind, a.dimension, list(a.skill_ids or []), a.logic or "all", a.min_years,
                   a.weight or 1.0)

    @property
    def retrievable(self) -> bool:
        return self.dimension != "logistics" and not (self.dimension == "experience" and not self.skill_ids)


@dataclass
class JobSpec:
    title: str
    atoms: list[AtomSpec]
    seniority_years: float | None = None
    location: str | None = None
    work_mode: str | None = None


@dataclass
class UnitView:
    id: str
    kind: str
    title: str
    context_header: str
    text: str
    start_year: float | None
    end_year: float | None


@dataclass
class ClaimView:
    skill_id: str
    unit_id: str
    level: str
    confidence: float
    quote: str
    via: str


@dataclass
class CandidateView:
    id: str
    headline: str
    total_years: float
    location: str | None
    work_mode: str | None
    notice_period_days: int | None
    flags: list[str]
    extraction_mode: str
    units: dict[str, UnitView]
    claims: dict[str, list[ClaimView]]  # skill_id -> strongest first


def _claim_key(c: ClaimView, units: dict[str, UnitView]) -> tuple:
    kind = units[c.unit_id].kind if c.unit_id in units else "other"
    kind_rank = {"experience": 0, "project": 1, "achievement": 2}.get(kind, 3)
    return (LEVEL_RANK[c.level], 0 if c.via in ("direct", "llm") else 1, kind_rank, -c.confidence)


def load_candidates(session: Session, tenant_id: str, ids: list[str]) -> dict[str, CandidateView]:
    if not ids:
        return {}
    out: dict[str, CandidateView] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        cands = session.scalars(select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.id.in_(chunk)))
        for c in cands:
            out[c.id] = CandidateView(c.id, c.headline, c.total_years or 0.0, c.location, c.work_mode,
                                      c.notice_period_days, list(c.flags or []), c.extraction_mode, {}, {})
        for u in session.scalars(select(EvidenceUnit).where(EvidenceUnit.tenant_id == tenant_id,
                                                            EvidenceUnit.candidate_id.in_(chunk))):
            if u.candidate_id in out:
                out[u.candidate_id].units[u.id] = UnitView(u.id, u.kind, u.title, u.context_header, u.text,
                                                           u.start_year, u.end_year)
        for cl in session.scalars(select(SkillClaim).where(SkillClaim.tenant_id == tenant_id,
                                                           SkillClaim.candidate_id.in_(chunk))):
            if cl.candidate_id in out:
                out[cl.candidate_id].claims.setdefault(cl.skill_id, []).append(
                    ClaimView(cl.skill_id, cl.unit_id, cl.level, cl.confidence, cl.quote, cl.via))
    for cv in out.values():
        for sid in cv.claims:
            cv.claims[sid].sort(key=lambda c: _claim_key(c, cv.units))
    return out
