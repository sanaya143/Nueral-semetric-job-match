"""FastAPI application: /api/v1 (initial local build).

Tenant: `X-Tenant-ID` header (default from settings). Authentication (OIDC) and Postgres
RLS arrive with production readiness (ADR 0009); this build is for local use only and
binds to 127.0.0.1 by default.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from nsm.config import PROJECT_ROOT, get_settings
from nsm.db import audit, get_scoped, get_session, init_db
from nsm.gateway import get_gateway
from nsm.ingestion.pipeline import create_pending, erase_candidate, process_candidate
from nsm.jobs.service import atoms_from_payload, confirm_job, create_job, update_atoms
from nsm.matching import service as matching
from nsm.models import AuditLog, Candidate, Feedback, Job, LlmCall, MatchResult, MatchRun, SkillClaim
from nsm.taxonomy import LEVEL_RANK, get_taxonomy

log = logging.getLogger("nsm.api")
ALLOWED_EXT = {".pdf", ".docx", ".txt", ".md"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ZIP_TOTAL = 60 * 1024 * 1024
MAX_ZIP_FILES = 500
_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

@asynccontextmanager
async def lifespan(_app: FastAPI):  # noqa: ANN201
    init_db()
    yield


app = FastAPI(title="Neural-Semantic Matching (initial local build)", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    t = x_tenant_id or get_settings().default_tenant
    if not _TENANT.match(t):
        raise HTTPException(400, "invalid X-Tenant-ID")
    return t


def _404(what: str) -> HTTPException:
    return HTTPException(404, f"{what} not found")


# ---------------------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------------------

@app.get("/api/v1/health")
def health() -> dict:
    gw = get_gateway()
    return {"status": "ok", "llm_enabled": gw.llm_enabled, "embedding_model": gw.embedding_model,
            "rerank": gw.rerank_enabled}


@app.get("/api/v1/config")
def config() -> dict:
    s = get_settings()
    gw = get_gateway()
    return {"llm_mode": s.llm_mode, "llm_enabled": gw.llm_enabled, "api_key_configured": bool(s.openrouter_api_key),
            "embedding_model": gw.embedding_model, "rerank_enabled": gw.rerank_enabled,
            "models": {t: gw.model_for(t) for t in ("extract", "escalate", "jd", "verify", "deep")},
            "caps": {"per_run_usd": s.cap_per_run_usd, "per_cv_usd": s.cap_per_cv_usd,
                     "per_cv_hard_usd": s.cap_per_cv_hard_usd, "monthly_usd": s.cap_monthly_usd,
                     "alert_fraction": s.alert_fraction},
            "funnel": {"stage1_top_k": s.stage1_top_k, "stage2_top_k": s.stage2_top_k,
                       "deep_eval_eager_k": s.deep_eval_eager_k, "deep_eval_lazy_k": s.deep_eval_lazy_k},
            "storage": {"database": "sqlite (local file)", "data_dir": str(s.data_dir)}}


@app.get("/api/v1/taxonomy")
def taxonomy() -> dict:
    tax = get_taxonomy()
    return {"version": tax.version, "skills": [{"id": s.id, "name": s.name, "category": s.category}
                                               for s in sorted(tax.skills.values(), key=lambda s: s.name.lower())]}


@app.get("/api/v1/stats")
def stats(t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    by_status = dict(s.execute(select(Candidate.status, func.count()).where(Candidate.tenant_id == t)
                               .group_by(Candidate.status)).all())
    jobs = s.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == t)) or 0
    runs = s.scalar(select(func.count()).select_from(MatchRun).where(MatchRun.tenant_id == t)) or 0
    gw = get_gateway()
    month = gw.spent(s, month=True)
    cfg = get_settings()
    recent = s.scalars(select(MatchRun).where(MatchRun.tenant_id == t).order_by(MatchRun.created_at.desc()).limit(5))
    return {"candidates": by_status, "candidates_total": sum(by_status.values()), "jobs": jobs, "runs": runs,
            "spend_month_usd": round(month, 6), "cap_monthly_usd": cfg.cap_monthly_usd,
            "spend_alert": month >= cfg.alert_fraction * cfg.cap_monthly_usd,
            "recent_runs": [_run_summary(s, r) for r in recent]}


# ---------------------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------------------

def _expand_upload(name: str, data: bytes) -> list[tuple[str, bytes]]:
    ext = Path(name).suffix.lower()
    if ext == ".zip":
        out: list[tuple[str, bytes]] = []
        total = 0
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir() or Path(info.filename).suffix.lower() not in ALLOWED_EXT:
                    continue
                if len(out) >= MAX_ZIP_FILES:
                    break
                total += info.file_size
                if info.file_size > MAX_FILE_BYTES or total > MAX_ZIP_TOTAL:
                    raise HTTPException(413, "zip too large")
                out.append((Path(info.filename).name, z.read(info)))
        return out
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"unsupported file type {ext}; use PDF, DOCX, TXT, MD or ZIP")
    return [(name, data)]


@app.post("/api/v1/candidates/upload")
async def upload(background: BackgroundTasks, files: list[UploadFile] = File(...), t: str = Depends(tenant),
                 s: Session = Depends(get_session)) -> dict:
    created = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_ZIP_TOTAL:
            raise HTTPException(413, "file too large")
        for name, content in _expand_upload(f.filename or "upload", data):
            if len(content) > MAX_FILE_BYTES:
                created.append({"filename": name, "status": "rejected", "error": "file larger than 10 MB"})
                continue
            cand = create_pending(s, t, name, content)
            created.append({"id": cand.id, "filename": name, "status": cand.status})
    s.commit()
    for c in created:
        if "id" in c:
            background.add_task(process_candidate, c["id"], t)
    return {"uploaded": created}


def _cand_row(c: Candidate) -> dict:
    return {"id": c.id, "display_name": (c.contact.full_name if c.contact and c.contact.full_name
                                         else f"Candidate {c.id[:6].upper()}"),
            "headline": c.headline, "status": c.status, "error": c.error, "total_years": c.total_years,
            "location": c.location, "work_mode": c.work_mode, "notice_period_days": c.notice_period_days,
            "flags": c.flags, "extraction_mode": c.extraction_mode, "source": c.source,
            "filename": c.documents[0].filename if c.documents else None,
            "created_at": c.created_at.isoformat() if c.created_at else None}


@app.get("/api/v1/candidates")
def list_candidates(status: str | None = None, q: str | None = None, limit: int = Query(100, le=500),
                    offset: int = 0, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    stmt = select(Candidate).where(Candidate.tenant_id == t)
    if status:
        stmt = stmt.where(Candidate.status == status)
    if q:
        stmt = stmt.where(Candidate.headline.ilike(f"%{q}%"))
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = s.scalars(stmt.order_by(Candidate.created_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [_cand_row(c) for c in rows]}


@app.get("/api/v1/candidates/{cid}")
def get_candidate(cid: str, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    c = get_scoped(s, Candidate, t, cid)
    if c is None:
        raise _404("candidate")
    tax = get_taxonomy()
    best: dict[str, SkillClaim] = {}
    for cl in c.claims:
        old = best.get(cl.skill_id)
        if old is None or LEVEL_RANK[cl.level] < LEVEL_RANK[old.level] or (
                cl.level == old.level and cl.via in ("direct", "llm") and old.via not in ("direct", "llm")):
            best[cl.skill_id] = cl
    units = {u.id: u for u in c.units}
    capabilities = sorted(
        [{"skill_id": sid, "name": tax.name(sid), "category": tax.skills[sid].category if sid in tax.skills else "",
          "level": cl.level, "quote": cl.quote, "via": cl.via, "confidence": cl.confidence,
          "context": units[cl.unit_id].context_header if cl.unit_id in units else ""}
         for sid, cl in best.items()], key=lambda x: (LEVEL_RANK[x["level"]], x["name"].lower()))
    audit(s, t, "candidate.viewed", "candidate", cid, {}, actor="recruiter")
    return {**_cand_row(c),
            "contact": ({"full_name": c.contact.full_name, "email": c.contact.email, "phone": c.contact.phone,
                         "redacted_fields": c.contact.redacted_fields} if c.contact else None),
            "consent_basis": c.consent_basis,
            "units": [{"id": u.id, "kind": u.kind, "title": u.title, "context": u.context_header, "text": u.text,
                       "start_year": u.start_year, "end_year": u.end_year}
                      for u in sorted(c.units, key=lambda u: u.start_char)],
            "capabilities": capabilities,
            "hidden_text_removed": any(d.hidden_text_removed for d in c.documents)}


@app.delete("/api/v1/candidates/{cid}")
def delete_candidate(cid: str, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    if get_scoped(s, Candidate, t, cid) is None:
        raise _404("candidate")
    verified = erase_candidate(s, t, cid)
    return {"deleted": True, "verified": verified}


@app.get("/api/v1/candidates/{cid}/file")
def candidate_file(cid: str, t: str = Depends(tenant), s: Session = Depends(get_session)):  # noqa: ANN201
    c = get_scoped(s, Candidate, t, cid)
    if c is None or not c.documents:
        raise _404("candidate file")
    d = c.documents[0]
    p = Path(d.storage_path)
    uploads = get_settings().uploads_dir.resolve()
    if not p.exists() or uploads not in p.resolve().parents:
        raise _404("file")
    audit(s, t, "candidate.file_downloaded", "candidate", cid, {}, actor="recruiter")
    return FileResponse(p, filename=d.filename)


# ---------------------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------------------

class JobIn(BaseModel):
    title: str = Field(min_length=2, max_length=250)
    description: str = Field(min_length=20, max_length=30000)
    use_llm: bool = True


class AtomIn(BaseModel):
    text: str
    kind: str = "must"
    dimension: str = "technical"
    skill_ids: list[str] | None = None
    logic: str = "all"
    min_years: float | None = None
    weight: float = 1.0


class AtomsIn(BaseModel):
    atoms: list[AtomIn]
    seniority_years: float | None = None
    location: str | None = None
    work_mode: str | None = None


def _job_out(s: Session, j: Job, notes: list[str] | None = None) -> dict:
    tax = get_taxonomy()
    runs = s.scalars(select(MatchRun).where(MatchRun.job_id == j.id).order_by(MatchRun.created_at.desc())).all()
    return {"id": j.id, "title": j.title, "description": j.description, "status": j.status, "version": j.version,
            "seniority_years": j.seniority_years, "location": j.location, "work_mode": j.work_mode,
            "extraction_mode": j.extraction_mode, "created_at": j.created_at.isoformat() if j.created_at else None,
            "atoms": [{"id": a.id, "text": a.text, "kind": a.kind, "dimension": a.dimension,
                       "skill_ids": a.skill_ids, "skills": [{"id": x, "name": tax.name(x)} for x in a.skill_ids],
                       "logic": a.logic, "min_years": a.min_years, "weight": a.weight} for a in j.atoms],
            "runs": [_run_summary(s, r) for r in runs], "notes": notes or []}


@app.post("/api/v1/jobs")
def post_job(body: JobIn, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    job, draft = create_job(s, get_gateway(), t, body.title, body.description, use_llm=body.use_llm)
    s.flush()
    return _job_out(s, job, draft.notes)


@app.get("/api/v1/jobs")
def list_jobs(t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    jobs = s.scalars(select(Job).where(Job.tenant_id == t).order_by(Job.created_at.desc())).all()
    out = []
    for j in jobs:
        last = s.scalars(select(MatchRun).where(MatchRun.job_id == j.id).order_by(MatchRun.created_at.desc())
                         .limit(1)).first()
        out.append({"id": j.id, "title": j.title, "status": j.status, "version": j.version,
                    "atoms": len(j.atoms), "must": sum(1 for a in j.atoms if a.kind == "must"),
                    "last_run": _run_summary(s, last) if last else None,
                    "created_at": j.created_at.isoformat() if j.created_at else None})
    return {"items": out}


def _job(s: Session, t: str, jid: str) -> Job:
    j = get_scoped(s, Job, t, jid)
    if j is None:
        raise _404("job")
    return j


@app.get("/api/v1/jobs/{jid}")
def get_job(jid: str, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    return _job_out(s, _job(s, t, jid))


@app.put("/api/v1/jobs/{jid}/requirements")
def put_requirements(jid: str, body: AtomsIn, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    j = _job(s, t, jid)
    update_atoms(s, j, atoms_from_payload([a.model_dump() for a in body.atoms]),
                 seniority_years=body.seniority_years, location=body.location, work_mode=body.work_mode)
    return _job_out(s, j)


@app.post("/api/v1/jobs/{jid}/confirm")
def post_confirm(jid: str, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    j = _job(s, t, jid)
    try:
        confirm_job(s, j)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _job_out(s, j)


@app.delete("/api/v1/jobs/{jid}")
def delete_job(jid: str, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    j = _job(s, t, jid)
    s.delete(j)
    audit(s, t, "job.deleted", "job", jid, {}, actor="recruiter")
    return {"deleted": True}


class MatchIn(BaseModel):
    verify: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/v1/jobs/{jid}/match")
def post_match(jid: str, body: MatchIn, background: BackgroundTasks, t: str = Depends(tenant),
               s: Session = Depends(get_session)) -> dict:
    j = _job(s, t, jid)
    try:
        run = matching.start_run(s, t, j, verify=body.verify, filters=body.filters)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    s.commit()
    background.add_task(matching.execute_run, run.id, t)
    return _run_summary(s, run)


# ---------------------------------------------------------------------------------------
# runs, reports, compare, feedback
# ---------------------------------------------------------------------------------------

def _run_summary(s: Session, r: MatchRun) -> dict:
    job = s.get(Job, r.job_id)
    return {"id": r.id, "job_id": r.job_id, "job_title": job.title if job else None, "job_version": r.job_version,
            "stale": bool(job and job.version != r.job_version), "status": r.status, "mode": r.mode,
            "error": r.error, "cost_usd": round(r.cost_usd or 0.0, 6),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


def _run(s: Session, t: str, rid: str) -> MatchRun:
    r = get_scoped(s, MatchRun, t, rid)
    if r is None:
        raise _404("run")
    return r


@app.get("/api/v1/runs/{rid}")
def get_run(rid: str, blind: bool = True, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    r = _run(s, t, rid)
    rows = s.scalars(select(MatchResult).where(MatchResult.run_id == r.id).order_by(MatchResult.rank)).all()
    cands = {c.id: c for c in s.scalars(select(Candidate).where(Candidate.id.in_([x.candidate_id for x in rows])))}
    fb = {f.candidate_id: f.action for f in s.scalars(select(Feedback).where(Feedback.run_id == r.id)
                                                       .order_by(Feedback.created_at))}
    results = []
    for x in rows:
        row = matching.result_row(x, cands[x.candidate_id], matching.display_name(cands[x.candidate_id], blind))
        row["feedback"] = fb.get(x.candidate_id)
        results.append(row)
    return {**_run_summary(s, r), "stats": r.stats, "results": results}


@app.get("/api/v1/runs/{rid}/candidates/{cid}")
def get_report(rid: str, cid: str, blind: bool = True, t: str = Depends(tenant),
               s: Session = Depends(get_session)) -> dict:
    rep = matching.report(s, t, _run(s, t, rid), cid, blind=blind)
    if rep is None:
        raise _404("result")
    return rep


@app.post("/api/v1/runs/{rid}/candidates/{cid}/deep-eval")
def post_deep_eval(rid: str, cid: str, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    de = matching.lazy_deep_eval(s, t, _run(s, t, rid), cid)
    if de is None:
        raise _404("result")
    return de


@app.get("/api/v1/runs/{rid}/compare")
def get_compare(rid: str, ids: str, blind: bool = True, t: str = Depends(tenant),
                s: Session = Depends(get_session)) -> dict:
    cids = [x for x in ids.split(",") if x][:3]
    if len(cids) < 2:
        raise HTTPException(400, "pick 2 or 3 candidates")
    return matching.compare(s, t, _run(s, t, rid), cids, blind=blind)


class FeedbackIn(BaseModel):
    run_id: str
    candidate_id: str
    action: str = Field(pattern="^(shortlist|maybe|reject)$")
    reason_code: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


@app.post("/api/v1/feedback")
def post_feedback(body: FeedbackIn, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    _run(s, t, body.run_id)
    if get_scoped(s, Candidate, t, body.candidate_id) is None:
        raise _404("candidate")
    s.add(Feedback(tenant_id=t, run_id=body.run_id, candidate_id=body.candidate_id, action=body.action,
                   reason_code=body.reason_code, note=body.note))
    audit(s, t, "feedback", "candidate", body.candidate_id, {"run_id": body.run_id, "action": body.action,
                                                              "reason": body.reason_code}, actor="recruiter")
    return {"ok": True}


# ---------------------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------------------

class SearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    use_llm: bool = False
    limit: int = Field(20, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/v1/search")
def post_search(body: SearchIn, blind: bool = True, t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    return matching.search(s, t, body.query, use_llm=body.use_llm, limit=body.limit, filters=body.filters,
                           blind=blind)


# ---------------------------------------------------------------------------------------
# admin: cost meter and audit log
# ---------------------------------------------------------------------------------------

@app.get("/api/v1/admin/costs")
def admin_costs(t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    cfg = get_settings()
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    by_task = s.execute(
        select(LlmCall.task, LlmCall.model, func.count(), func.sum(LlmCall.input_tokens),
               func.sum(LlmCall.cached_tokens), func.sum(LlmCall.output_tokens), func.sum(LlmCall.cost_usd),
               func.sum(case((LlmCall.ok, 0), else_=1)), func.avg(LlmCall.latency_ms),
               func.sum(LlmCall.reasoning_tokens))
        .where(LlmCall.tenant_id == t).group_by(LlmCall.task, LlmCall.model)).all()
    runs = s.scalars(select(MatchRun).where(MatchRun.tenant_id == t).order_by(MatchRun.created_at.desc()).limit(20))
    n_ready = s.scalar(select(func.count()).select_from(Candidate).where(Candidate.tenant_id == t,
                                                                         Candidate.status == "ready")) or 0
    cv_cost = s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
        LlmCall.tenant_id == t, LlmCall.task.in_(["extract", "escalate", "embed"]), LlmCall.run_id.is_(None))) or 0.0
    return {
        "caps": {"per_run_usd": cfg.cap_per_run_usd, "per_cv_usd": cfg.cap_per_cv_usd,
                 "per_cv_hard_usd": cfg.cap_per_cv_hard_usd, "monthly_usd": cfg.cap_monthly_usd},
        "month_to_date_usd": round(float(s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
                                                  .where(LlmCall.created_at >= month_start)) or 0.0), 6),
        "tenant_total_usd": round(sum(float(r[6] or 0) for r in by_task), 6),
        "avg_cost_per_cv_usd": round(float(cv_cost) / n_ready, 6) if n_ready else 0.0,
        "by_task": [{"task": r[0], "model": r[1], "calls": r[2], "input_tokens": int(r[3] or 0),
                     "cached_tokens": int(r[4] or 0), "output_tokens": int(r[5] or 0),
                     "cost_usd": round(float(r[6] or 0), 6), "failed": int(r[7] or 0),
                     "avg_latency_ms": int(r[8] or 0), "reasoning_tokens": int(r[9] or 0)} for r in by_task],
        "runs": [_run_summary(s, r) for r in runs],
        "note": "Offline mode costs $0. In OpenRouter mode, costs come from provider-reported usage "
                "(or list prices) plus the 5.5% credit fee.",
    }


@app.get("/api/v1/admin/audit")
def admin_audit(limit: int = Query(200, le=1000), t: str = Depends(tenant), s: Session = Depends(get_session)) -> dict:
    rows = s.scalars(select(AuditLog).where(AuditLog.tenant_id == t).order_by(AuditLog.at.desc()).limit(limit))
    return {"items": [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity,
                       "entity_id": a.entity_id, "detail": a.detail} for a in rows]}


# ---------------------------------------------------------------------------------------
# frontend (built React app), if present
# ---------------------------------------------------------------------------------------

_DIST = PROJECT_ROOT / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):  # noqa: ANN201
        if path.startswith("api/"):
            raise _404("endpoint")
        f = _DIST / path
        if path and f.is_file() and _DIST.resolve() in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(_DIST / "index.html")
