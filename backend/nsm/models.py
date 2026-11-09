"""ORM models (SQLAlchemy 2). Portable to PostgreSQL later (see ADR 0009).

Every tenant-scoped table carries `tenant_id`; all queries go through tenant-filtered
helpers in `nsm.db`. Contact PII lives only in `candidate_contacts` (the PII vault).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), default="upload")
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    headline: Mapped[str] = mapped_column(String(256), default="")
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notice_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_years: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="processing")  # processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_basis: Mapped[str] = mapped_column(String(64), default="customer_controller")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    extraction_mode: Mapped[str] = mapped_column(String(32), default="heuristic")
    extraction_version: Mapped[str] = mapped_column(String(32), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contact: Mapped["CandidateContact | None"] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", uselist=False, passive_deletes=True
    )
    documents: Mapped[list["Document"]] = relationship(back_populates="candidate", cascade="all, delete-orphan",
                                                         passive_deletes=True)
    units: Mapped[list["EvidenceUnit"]] = relationship(back_populates="candidate", cascade="all, delete-orphan",
                                                         passive_deletes=True)
    claims: Mapped[list["SkillClaim"]] = relationship(back_populates="candidate", cascade="all, delete-orphan",
                                                         passive_deletes=True)


class CandidateContact(Base):
    """PII vault: never sent to an LLM, never used for scoring."""

    __tablename__ = "candidate_contacts"
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redacted_fields: Mapped[list] = mapped_column(JSON, default=list)
    candidate: Mapped[Candidate] = relationship(back_populates="contact")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    mime: Mapped[str] = mapped_column(String(128), default="")
    storage_path: Mapped[str] = mapped_column(String(512))
    evaluable_text: Mapped[str] = mapped_column(Text, default="")  # sanitized + PII-redacted
    hidden_text_removed: Mapped[bool] = mapped_column(Boolean, default=False)
    candidate: Mapped[Candidate] = relationship(back_populates="documents")


class EvidenceUnit(Base):
    __tablename__ = "evidence_units"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # experience|project|skills|summary|education|other
    title: Mapped[str] = mapped_column(String(256), default="")
    context_header: Mapped[str] = mapped_column(String(512), default="")
    text: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int] = mapped_column(Integer, default=0)
    end_char: Mapped[int] = mapped_column(Integer, default=0)
    start_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate: Mapped[Candidate] = relationship(back_populates="units")


class SkillClaim(Base):
    """Capability claim: evidence unit -> canonical skill, with an evidence level."""

    __tablename__ = "skill_claims"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("evidence_units.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16))  # demonstrated|applied|inferred|listed_only
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    quote: Mapped[str] = mapped_column(Text, default="")
    via: Mapped[str] = mapped_column(String(64), default="direct")  # direct|is_a:<child>|implies:<src>|llm
    candidate: Mapped[Candidate] = relationship(back_populates="claims")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|confirmed
    seniority_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    extraction_mode: Mapped[str] = mapped_column(String(32), default="heuristic")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    atoms: Mapped[list["RequirementAtom"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="RequirementAtom.position"
    )


class RequirementAtom(Base):
    __tablename__ = "requirement_atoms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="must")  # must|nice|learnable
    dimension: Mapped[str] = mapped_column(String(16), default="technical")
    skill_ids: Mapped[list] = mapped_column(JSON, default=list)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    min_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    logic: Mapped[str] = mapped_column(String(8), default="all")  # all|any over skill_ids
    job: Mapped[Job] = relationship(back_populates="atoms")


class MatchRun(Base):
    __tablename__ = "match_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    job_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|done|failed
    mode: Mapped[str] = mapped_column(String(32), default="offline")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MatchResult(Base):
    __tablename__ = "match_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("match_runs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    gate_passed: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_band: Mapped[str] = mapped_column(String(16))
    suitability: Mapped[str] = mapped_column(String(32))
    stage1_score: Mapped[float] = mapped_column(Float, default=0.0)
    dimension_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    verdicts: Mapped[list] = mapped_column(JSON, default=list)
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    deep_eval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verifier: Mapped[str] = mapped_column(String(32), default="offline")


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("match_runs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(16))  # shortlist|maybe|reject
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LlmCall(Base):
    """Cost meter: one row per external model call."""

    __tablename__ = "llm_calls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
