"""Match runs, reports, comparison and natural-language search."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nsm.config import get_settings
from nsm.db import audit, get_scoped, session_scope
from nsm.extraction.llm import PROMPT_VERSION as EXTRACT_PV
from nsm.gateway import Gateway, GatewayUnavailable, get_gateway
from nsm.jobs.requirements import PROMPT_VERSION as JD_PV
from nsm.jobs.requirements import parse_jd_llm, parse_query_heuristic
from nsm.matching.deep_eval import PROMPT_VERSION as DEEP_PV
from nsm.matching.deep_eval import llm_deep_eval, offline_deep_eval
from nsm.matching.graph import build_graph, stage0_allowed
from nsm.models import Candidate, Job, LlmCall, MatchResult, MatchRun
from nsm.retrieval.hybrid import retrieve
from nsm.scoring.scorer import SCORER_VERSION, score_candidate, sort_key
from nsm.scoring.types import AtomSpec, JobSpec, load_candidates
from nsm.scoring.verdicts import PROMPT_VERSION as VERIFY_PV
from nsm.scoring.verdicts import offline_verdicts
from nsm.taxonomy import get_taxonomy

log = logging.getLogger("nsm.matching")


def job_spec(job: Job) -> JobSpec:
    return JobSpec(job.title, [AtomSpec.from_row(a) for a in job.atoms], job.seniority_years, job.location,
                   job.work_mode)


def provenance(gateway: Gateway, mode: str) -> dict:
    s = gateway.s
    return {"mode": mode, "scorer": SCORER_VERSION, "taxonomy": get_taxonomy().version,
            "prompts": {"extract": EXTRACT_PV, "jd": JD_PV, "verify": VERIFY_PV, "deep": DEEP_PV},
            "models": ({"verify": gateway.model_for("verify"), "deep": gateway.model_for("deep")}
                       if mode == "llm" else {}),
            "embedding_model": gateway.embedding_model}


def start_run(session: Session, tenant_id: str, job: Job, *, verify: bool = True, filters: dict | None = None
              ) -> MatchRun:
    if job.status != "confirmed":
        raise ValueError("confirm the job's requirements before running a match")
    gw = get_gateway()
    mode = "llm" if (gw.llm_enabled and verify) else "offline"
    run = MatchRun(tenant_id=tenant_id, job_id=job.id, job_version=job.version, status="queued", mode=mode,
                   stats={"filters": filters or {}, "verify": verify})
    session.add(run)
    session.flush()
    audit(session, tenant_id, "run.created", "match_run", run.id, {"job_id": job.id, "mode": mode}, actor="recruiter")
    return run


def execute_run(run_id: str, tenant_id: str, gateway: Gateway | None = None) -> str:
    gw = gateway or get_gateway()
    settings = get_settings()
    with session_scope() as s:
        run = get_scoped(s, MatchRun, tenant_id, run_id)
        if run is None:
            return "missing"
        job = get_scoped(s, Job, tenant_id, run.job_id)
        run.status = "running"
        s.flush()
        try:
            spec = job_spec(job)
            graph = build_graph(s, gw, settings, tenant_id, spec, run_id)
            final = graph.invoke({"filters": run.stats.get("filters") or {}, "verify": run.stats.get("verify", True),
                                  "stats": {}})
            results = final.get("results") or []
            for r in results:
                s.add(MatchResult(tenant_id=tenant_id, run_id=run.id, candidate_id=r["candidate_id"], rank=r["rank"],
                                  score=r["score"], gate_passed=r["gate_passed"], confidence=r["confidence"],
                                  confidence_band=r["confidence_band"], suitability=r["suitability"],
                                  stage1_score=r["stage1_score"], dimension_scores=r["dimension_scores"],
                                  verdicts=r["verdicts"], concerns=r["concerns"], deep_eval=r.get("deep_eval"),
                                  verifier=r["verifier"]))
            s.flush()
            cost = s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.run_id == run.id))
            stats = dict(final.get("stats") or {})
            stats.update(filters=run.stats.get("filters") or {}, verify=run.stats.get("verify", True),
                         provenance=provenance(gw, run.mode), results=len(results))
            run.stats, run.cost_usd = stats, float(cost or 0.0)
            run.status, run.finished_at = "done", datetime.now(timezone.utc)
        except Exception as e:  # noqa: BLE001
            log.exception("match run %s failed", run_id)
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"[:500]
            run.finished_at = datetime.now(timezone.utc)
        audit(s, tenant_id, "run.finished", "match_run", run.id, {"status": run.status, "cost_usd": run.cost_usd})
        return run.status


def run_match_sync(tenant_id: str, job_id: str, *, verify: bool = True, filters: dict | None = None,
                   gateway: Gateway | None = None) -> str:
    with session_scope() as s:
        job = get_scoped(s, Job, tenant_id, job_id)
        run_id = start_run(s, tenant_id, job, verify=verify, filters=filters).id
    execute_run(run_id, tenant_id, gateway)
    return run_id


# ---------------------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------------------

def _candidate_public(c: Candidate) -> dict:
    """Recruiter-facing candidate summary. Contact PII is shown only in the profile view."""
    return {"id": c.id, "headline": c.headline, "total_years": c.total_years, "location": c.location,
            "work_mode": c.work_mode, "notice_period_days": c.notice_period_days, "flags": c.flags,
            "extraction_mode": c.extraction_mode, "source": c.source}


def result_row(r: MatchResult, c: Candidate, display_name: str | None) -> dict:
    must = [v for v in r.verdicts if v["kind"] == "must"]
    return {"candidate": {**_candidate_public(c), "display_name": display_name}, "rank": r.rank, "score": r.score,
            "gate_passed": r.gate_passed, "confidence": r.confidence, "confidence_band": r.confidence_band,
            "suitability": r.suitability, "stage1_score": r.stage1_score, "dimension_scores": r.dimension_scores,
            "verifier": r.verifier,
            "coverage": {"must_total": len(must), "must_met": sum(1 for v in must if v["status"] == "met"),
                         "demonstrated": sum(1 for v in r.verdicts if v["level"] == "demonstrated")},
            "top_gaps": [v["atom_text"] for v in must if v["status"] != "met"][:3],
            "concern_count": len(r.concerns)}


def display_name(c: Candidate, blind: bool) -> str | None:
    if blind or c.contact is None or not c.contact.full_name:
        return f"Candidate {c.id[:6].upper()}"
    return c.contact.full_name


def report(session: Session, tenant_id: str, run: MatchRun, candidate_id: str, blind: bool = True) -> dict | None:
    r = session.scalars(select(MatchResult).where(MatchResult.tenant_id == tenant_id, MatchResult.run_id == run.id,
                                                  MatchResult.candidate_id == candidate_id)).first()
    if r is None:
        return None
    c = get_scoped(session, Candidate, tenant_id, candidate_id)
    job = get_scoped(session, Job, tenant_id, run.job_id)
    units = sorted(c.units, key=lambda u: (u.start_char,))
    return {
        **result_row(r, c, display_name(c, blind)),
        "verdicts": r.verdicts, "concerns": r.concerns, "deep_eval": r.deep_eval,
        "job": {"id": job.id, "title": job.title, "version": job.version, "run_version": run.job_version,
                "stale": job.version != run.job_version},
        "evidence_units": [{"id": u.id, "kind": u.kind, "context": u.context_header, "text": u.text}
                           for u in units if u.kind in ("experience", "project", "achievement", "education")],
        "provenance": (run.stats or {}).get("provenance", {}),
        "disclaimer": "Evidence-strength assessment, not a hiring decision. Verdicts cite CV text; "
                      "'inferred' means not stated. The recruiter decides.",
    }


def lazy_deep_eval(session: Session, tenant_id: str, run: MatchRun, candidate_id: str) -> dict | None:
    """On-demand LLM deep evaluation (ranks 6-10 by default, any shortlisted candidate allowed)."""
    r = session.scalars(select(MatchResult).where(MatchResult.run_id == run.id,
                                                  MatchResult.candidate_id == candidate_id,
                                                  MatchResult.tenant_id == tenant_id)).first()
    if r is None:
        return None
    if r.deep_eval and r.deep_eval.get("mode") == "llm":
        return r.deep_eval
    gw = get_gateway()
    job = get_scoped(session, Job, tenant_id, run.job_id)
    spec = job_spec(job)
    cv = load_candidates(session, tenant_id, [candidate_id])[candidate_id]
    scored = {"score": r.score, "gate_passed": r.gate_passed, "confidence_band": r.confidence_band}
    try:
        de = llm_deep_eval(session, gw, tenant_id, run.id, spec, cv, r.verdicts, scored)
    except GatewayUnavailable as e:
        de = offline_deep_eval(spec, cv, r.verdicts, {**scored, "confidence": r.confidence})
        de["note"] = f"LLM deep evaluation unavailable: {str(e)[:120]}"
    r.deep_eval = de
    run.cost_usd = float(session.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
                                        .where(LlmCall.run_id == run.id)) or 0.0)
    return de


def compare(session: Session, tenant_id: str, run: MatchRun, candidate_ids: list[str], blind: bool = True) -> dict:
    rows = session.scalars(select(MatchResult).where(MatchResult.tenant_id == tenant_id, MatchResult.run_id == run.id,
                                                     MatchResult.candidate_id.in_(candidate_ids))).all()
    rows = sorted(rows, key=lambda r: r.rank)
    cands = {c.id: c for c in session.scalars(select(Candidate).where(Candidate.tenant_id == tenant_id,
                                                                      Candidate.id.in_(candidate_ids)))}
    atoms: list[dict] = []
    if rows:
        for i, v in enumerate(rows[0].verdicts):
            cells = []
            for r in rows:
                rv = r.verdicts[i]
                ev = rv["evidence"][0] if rv["evidence"] else {}
                cells.append({"candidate_id": r.candidate_id, "level": rv["level"], "status": rv["status"],
                              "quote": ev.get("quote", ""), "note": rv.get("note", "")})
            differs = len({c["level"] for c in cells}) > 1
            atoms.append({"atom_text": v["atom_text"], "kind": v["kind"], "dimension": v["dimension"],
                          "cells": cells, "differs": differs})
    return {"candidates": [result_row(r, cands[r.candidate_id], display_name(cands[r.candidate_id], blind))
                           for r in rows],
            "atoms": atoms,
            "differentiators": [a["atom_text"] for a in atoms if a["differs"]]}


# ---------------------------------------------------------------------------------------
# Natural-language search (same funnel, no LLM verification by default)
# ---------------------------------------------------------------------------------------

def search(session: Session, tenant_id: str, query: str, *, use_llm: bool = False, limit: int = 20,
           filters: dict | None = None, blind: bool = True) -> dict:
    gw = get_gateway()
    draft = (parse_jd_llm(session, gw, tenant_id, "Search", query, task="search") if use_llm and gw.llm_enabled
             else parse_query_heuristic(query))
    spec = JobSpec(draft.title, [AtomSpec(f"q{i}", a.text, a.kind, a.dimension, a.skill_ids, a.logic, a.min_years,
                                          a.weight) for i, a in enumerate(draft.atoms)],
                   draft.seniority_years, draft.location, draft.work_mode)
    if not spec.atoms:
        return {"query": query, "atoms": [], "results": [], "note": "No searchable requirement found in the query."}
    allowed, _ = stage0_allowed(session, tenant_id, filters)
    s1 = retrieve(session, gw, tenant_id, spec.atoms, allowed, get_settings().stage1_top_k)
    s1d = dict(s1.ranked)
    cvs = load_candidates(session, tenant_id, list(s1d))
    results = []
    for cid, cv in cvs.items():
        v = offline_verdicts(spec, cv, s1.semantic_hits.get(cid, {}))
        sc = score_candidate(spec, cv, v)
        results.append({"candidate_id": cid, "verdicts": v, "stage1_score": s1d.get(cid, 0.0), **sc})
    results.sort(key=sort_key)
    results = results[:limit]
    cands = {c.id: c for c in session.scalars(select(Candidate).where(
        Candidate.tenant_id == tenant_id, Candidate.id.in_([r["candidate_id"] for r in results])))}
    out = []
    for i, r in enumerate(results):
        c = cands[r["candidate_id"]]
        out.append({"rank": i + 1, "candidate": {**_candidate_public(c), "display_name": display_name(c, blind)},
                    "score": r["score"], "gate_passed": r["gate_passed"], "confidence_band": r["confidence_band"],
                    "suitability": r["suitability"], "verdicts": r["verdicts"], "concerns": r["concerns"]})
    audit(session, tenant_id, "search", "search", None, {"atoms": len(spec.atoms), "results": len(out)}, actor="recruiter")
    return {"query": query, "mode": draft.mode, "atoms": [a.__dict__ for a in spec.atoms], "results": out}
