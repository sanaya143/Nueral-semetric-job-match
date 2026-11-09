"""CV ingestion pipeline and verified erasure.

upload -> local file -> parse (hidden-text detection) -> sanitize (injection flags)
-> redact (PII vault + blind evaluation) -> dedup (before any LLM spend)
-> extract (heuristic, or LLM + validation) -> embed evidence units -> SQLite + FTS5
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from nsm.db import audit, fts_delete_candidate, fts_index_units, get_scoped, session_scope
from nsm.extraction.llm import extract_with_llm
from nsm.gateway import Gateway, get_gateway
from nsm.ingestion.dedup import content_hash
from nsm.ingestion.parsing import UnsupportedFormat, parse_document
from nsm.ingestion.pii import redact
from nsm.ingestion.sanitize import sanitize
from nsm.models import Candidate, CandidateContact, Document, EvidenceUnit, SkillClaim
from nsm.retrieval.vector_index import invalidate
from nsm.storage import delete_file, save_upload

log = logging.getLogger("nsm.ingestion")
EXTRACTION_VERSION = "v1"


def unit_embedding_text(u: EvidenceUnit) -> str:
    return f"{u.context_header}\n{u.text}" if u.context_header and u.context_header not in u.text else u.text


def create_pending(session: Session, tenant_id: str, filename: str, data: bytes, *, source: str = "upload",
                   external_id: str | None = None, consent_basis: str = "customer_controller") -> Candidate:
    path = save_upload(tenant_id, filename, data)
    cand = Candidate(tenant_id=tenant_id, source=source, external_id=external_id, content_hash="pending",
                     status="processing", consent_basis=consent_basis)
    session.add(cand)
    session.flush()
    session.add(Document(tenant_id=tenant_id, candidate_id=cand.id, filename=Path(filename).name[:250],
                         storage_path=str(path)))
    audit(session, tenant_id, "candidate.uploaded", "candidate", cand.id, {"source": source})
    session.flush()
    return cand


def _process(session: Session, cand: Candidate, gateway: Gateway, bulk: bool) -> None:
    doc = cand.documents[0]
    parsed = parse_document(doc.filename, Path(doc.storage_path).read_bytes())
    doc.mime = parsed.mime
    san = sanitize(parsed.text, parsed.hidden_snippets)
    red = redact(san.text)
    doc.evaluable_text = red.text
    doc.hidden_text_removed = bool(parsed.hidden_snippets)
    flags = list(san.flags)

    cand.content_hash = content_hash(red.text)
    dup = session.scalars(
        select(Candidate).where(Candidate.tenant_id == cand.tenant_id, Candidate.content_hash == cand.content_hash,
                                Candidate.id != cand.id, Candidate.status.in_(["ready", "processing"]))
    ).first()
    if dup is not None:
        cand.status, cand.error, cand.flags = "duplicate", f"duplicate_of:{dup.id}", flags + ["duplicate"]
        return

    session.add(CandidateContact(candidate_id=cand.id, tenant_id=cand.tenant_id, full_name=red.full_name,
                                 email=red.email, phone=red.phone, redacted_fields=red.redacted_fields))
    if len(red.text.strip()) < 80:
        cand.status, cand.error = "failed", "too little text (scanned PDF? OCR is not in the initial build)"
        cand.flags = flags
        return

    result = extract_with_llm(session, gateway, red.text, tenant_id=cand.tenant_id, candidate_id=cand.id, bulk=bulk)
    cand.headline = result.headline or ""
    cand.location, cand.work_mode = result.location, result.work_mode
    cand.notice_period_days, cand.total_years = result.notice_period_days, result.total_years
    cand.flags = flags + result.flags
    cand.extraction_mode, cand.extraction_version = result.mode, EXTRACTION_VERSION

    key_to_unit: dict[str, EvidenceUnit] = {}
    for u in result.units:
        eu = EvidenceUnit(tenant_id=cand.tenant_id, candidate_id=cand.id, kind=u.kind, title=u.title,
                          context_header=u.context_header, text=u.text, start_char=u.start, end_char=u.end,
                          start_year=u.start_year, end_year=u.end_year)
        session.add(eu)
        key_to_unit[u.key] = eu
    session.flush()
    for c in result.claims:
        eu = key_to_unit.get(c.unit_key)
        if eu is not None:
            session.add(SkillClaim(tenant_id=cand.tenant_id, candidate_id=cand.id, unit_id=eu.id, skill_id=c.skill_id,
                                   level=c.level, confidence=c.confidence, quote=c.quote[:1000], via=c.via))
    units = list(key_to_unit.values())
    if units:
        vecs, model = gateway.embed(session, [unit_embedding_text(u) for u in units], tenant_id=cand.tenant_id,
                                    candidate_id=cand.id)
        for u, v in zip(units, vecs):
            u.embedding = np.asarray(v, dtype=np.float32).tobytes()
            u.embedding_model = model
        fts_index_units(session, [(u.id, cand.tenant_id, cand.id, unit_embedding_text(u)) for u in units])
    cand.status = "ready"


def process_candidate(candidate_id: str, tenant_id: str, gateway: Gateway | None = None, bulk: bool = False) -> str:
    """Runs in a background task (own session). Returns the final status."""
    gw = gateway or get_gateway()
    with session_scope() as s:
        cand = get_scoped(s, Candidate, tenant_id, candidate_id)
        if cand is None:
            return "missing"
        try:
            _process(s, cand, gw, bulk)
        except UnsupportedFormat as e:
            cand.status, cand.error = "failed", str(e)
        except Exception as e:  # noqa: BLE001 - recorded on the candidate, never swallowed silently
            log.exception("ingestion failed for %s", candidate_id)
            s.rollback()
            cand = get_scoped(s, Candidate, tenant_id, candidate_id)
            if cand is None:
                return "missing"
            cand.status, cand.error = "failed", f"{type(e).__name__}: {e}"[:500]
        audit(s, tenant_id, "candidate.processed", "candidate", candidate_id,
              {"status": cand.status, "mode": cand.extraction_mode})
        status = cand.status
    invalidate(tenant_id)
    return status


def ingest_bytes(tenant_id: str, filename: str, data: bytes, *, source: str = "upload",
                 external_id: str | None = None, gateway: Gateway | None = None, bulk: bool = False) -> tuple[str, str]:
    """Synchronous ingest for scripts and tests. Returns (candidate_id, status)."""
    with session_scope() as s:
        cid = create_pending(s, tenant_id, filename, data, source=source, external_id=external_id).id
    return cid, process_candidate(cid, tenant_id, gateway, bulk)


def erase_candidate(session: Session, tenant_id: str, candidate_id: str, actor: str = "recruiter") -> bool:
    """Verified hard delete: rows (cascade), FTS rows, vectors (in rows), raw files, caches."""
    cand = get_scoped(session, Candidate, tenant_id, candidate_id)
    if cand is None:
        return False
    paths = [d.storage_path for d in cand.documents]
    fts_delete_candidate(session, tenant_id, candidate_id)
    session.delete(cand)
    session.flush()
    files_deleted = sum(1 for p in paths if delete_file(p))
    remaining = {
        "units": session.scalar(select(EvidenceUnit.id).where(EvidenceUnit.candidate_id == candidate_id).limit(1)),
        "claims": session.scalar(select(SkillClaim.id).where(SkillClaim.candidate_id == candidate_id).limit(1)),
        "contact": session.get(CandidateContact, candidate_id),
    }
    leftovers = [k for k, v in remaining.items() if v is not None]
    audit(session, tenant_id, "candidate.erased", "candidate", candidate_id,
          {"files_deleted": files_deleted, "verified": not leftovers, "leftovers": leftovers}, actor=actor)
    invalidate(tenant_id)
    return not leftovers
