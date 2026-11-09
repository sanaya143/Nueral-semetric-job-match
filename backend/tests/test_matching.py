"""JD parsing, deterministic scoring, the match-run graph, dedup, erasure, fairness."""

from __future__ import annotations

from conftest import CV_A, CV_B, JD_AI
from sqlalchemy import select, text

from nsm.db import get_scoped, session_scope
from nsm.gateway import get_gateway
from nsm.ingestion.pipeline import erase_candidate, ingest_bytes
from nsm.jobs.requirements import parse_jd_heuristic, parse_query_heuristic
from nsm.jobs.service import confirm_job, create_job
from nsm.matching.service import job_spec, run_match_sync
from nsm.models import Candidate, CandidateContact, EvidenceUnit, Job, MatchResult, SkillClaim
from nsm.scoring.scorer import WEIGHTS, score_candidate
from nsm.scoring.types import load_candidates
from nsm.scoring.verdicts import offline_verdicts


def _job(tenant: str, title="AI Engineer", jd=JD_AI) -> str:
    with session_scope() as s:
        job, _ = create_job(s, get_gateway(), tenant, title, jd)
        confirm_job(s, job)
        return job.id


def test_jd_parser_builds_atoms_without_the_title():
    d = parse_jd_heuristic("AI Engineer", JD_AI)
    must = [a for a in d.atoms if a.kind == "must"]
    assert len(must) == 6
    assert not any("AI Engineer" == a.text for a in d.atoms)
    vec = next(a for a in d.atoms if "vector database" in a.text)
    assert vec.skill_ids == ["vector_db"] and vec.logic == "any"       # children collapsed into the parent
    rag = next(a for a in d.atoms if "RAG" in a.text)
    assert rag.skill_ids == ["rag"]                                    # implied component (embeddings) dropped
    exp = next(a for a in d.atoms if a.dimension == "experience")
    assert exp.min_years == 3 and d.seniority_years == 3
    logistics = [a for a in d.atoms if a.dimension == "logistics"]
    assert len(logistics) == 2 and all(a.kind == "nice" for a in logistics)  # never a silent hard filter
    assert d.location == "Bengaluru" and d.work_mode == "hybrid"


def test_jd_parser_section_phrasings():
    jd = ("Must have:\n- Deploying services to production\n- Python\n"
          "Nice to have:\n- Evaluation of LLM outputs\n"
          "Can learn on the job:\n- LangGraph\n")
    d = parse_jd_heuristic("Engineer", jd)
    by_text = {a.text: a for a in d.atoms}
    assert "Can learn on the job:" not in by_text                     # a heading, not a requirement
    assert by_text["LangGraph"].kind == "learnable"
    assert by_text["Deploying services to production"].skill_ids == ["production_deployment"]
    assert by_text["Evaluation of LLM outputs"].skill_ids == ["llm_evaluation"]


def test_query_parser():
    d = parse_query_heuristic("python developers with kafka and 5+ years in Pune")
    assert {s for a in d.atoms for s in a.skill_ids} >= {"python", "kafka"}
    assert any(a.dimension == "experience" and a.min_years == 5 for a in d.atoms)


def test_candidate_b_outranks_candidate_a():
    t = "t-ab"
    a_id, _ = ingest_bytes(t, "a.txt", CV_A.encode())
    b_id, _ = ingest_bytes(t, "b.txt", CV_B.encode())
    rid = run_match_sync(t, _job(t))
    with session_scope() as s:
        rows = {r.candidate_id: r for r in s.scalars(select(MatchResult).where(MatchResult.run_id == rid))}
    a, b = rows[a_id], rows[b_id]
    assert b.rank == 1 and b.gate_passed and b.score > a.score + 30
    assert not a.gate_passed
    b_rag = next(v for v in b.verdicts if "RAG" in v["atom_text"])
    assert b_rag["level"] == "demonstrated" and "document" in b_rag["evidence"][0]["quote"]
    assert not b_rag["evidence"][0]["quote"].startswith("- ")
    assert any(c["type"] == "needs_verification" for c in a.concerns)


def test_scoring_is_deterministic_and_never_penalises_over_experience():
    t = "t-score"
    b_id, _ = ingest_bytes(t, "b.txt", CV_B.encode())
    with session_scope() as s:
        job = get_scoped(s, Job, t, _job(t))
        spec = job_spec(job)
        cv = load_candidates(s, t, [b_id])[b_id]
        v = offline_verdicts(spec, cv)
        first, second = score_candidate(spec, cv, v), score_candidate(spec, cv, v)
        assert first == second
        cv.total_years = 25.0
        senior = score_candidate(spec, cv, offline_verdicts(spec, cv))
        assert senior["score"] >= first["score"]
    assert WEIGHTS["education"] == 0.0


def test_unknown_logistics_never_fail_the_gate():
    t = "t-logi"
    no_prefs = CV_B.split("Preferences")[0]
    cid, _ = ingest_bytes(t, "b.txt", no_prefs.encode())
    with session_scope() as s:
        job = get_scoped(s, Job, t, _job(t))
        for a in job.atoms:
            if a.dimension == "logistics":
                a.kind = "must"
        spec = job_spec(job)
        cv = load_candidates(s, t, [cid])[cid]
        res = score_candidate(spec, cv, offline_verdicts(spec, cv))
    assert res["gate_passed"]
    assert any(c["type"] == "unknown" for c in res["concerns"])


def test_duplicate_detected_before_any_spend():
    t = "t-dup"
    _, s1 = ingest_bytes(t, "b.txt", CV_B.encode())
    _, s2 = ingest_bytes(t, "b_again.txt", CV_B.encode())
    assert (s1, s2) == ("ready", "duplicate")


def test_tenant_isolation_in_retrieval():
    ingest_bytes("t-iso-1", "b.txt", CV_B.encode())
    rid = run_match_sync("t-iso-2", _job("t-iso-2"))
    with session_scope() as s:
        assert s.scalars(select(MatchResult).where(MatchResult.run_id == rid)).first() is None


def test_erasure_removes_rows_index_rows_and_file():
    t = "t-erase"
    cid, _ = ingest_bytes(t, "b.txt", CV_B.encode())
    with session_scope() as s:
        from pathlib import Path
        path = Path(get_scoped(s, Candidate, t, cid).documents[0].storage_path)
        assert path.exists()
        assert erase_candidate(s, t, cid)
    with session_scope() as s:
        assert s.get(Candidate, cid) is None and s.get(CandidateContact, cid) is None
        assert s.scalars(select(EvidenceUnit).where(EvidenceUnit.candidate_id == cid)).first() is None
        assert s.scalars(select(SkillClaim).where(SkillClaim.candidate_id == cid)).first() is None
        n = s.execute(text("SELECT count(*) FROM evidence_fts WHERE candidate_id = :c"), {"c": cid}).scalar()
        assert n == 0
    assert not path.exists()


def test_counterfactual_identity_changes_do_not_move_the_score():
    variants = {
        "base": CV_B,
        "name": CV_B.replace("Kavya Iyer", "Mohammed Qureshi"),
        "personal": CV_B + "\nPersonal Details\nDate of Birth: 01/01/1985\nReligion: Christian\n"
                           "Marital Status: Married\nCategory: SC\n",
        # same roles and durations, older role moved 2 years earlier (a career break before the current job)
        "career_gap": CV_B.replace("Jul 2019 to May 2021", "Jul 2017 to May 2019"),
    }
    scores = {}
    for name, cv_text in variants.items():
        t = f"t-fair-{name}"  # separate tenants: identical redacted text would otherwise dedup
        cid, _ = ingest_bytes(t, "cv.txt", cv_text.encode())
        rid = run_match_sync(t, _job(t))
        with session_scope() as s:
            scores[name] = s.scalars(select(MatchResult).where(MatchResult.run_id == rid)).first().score
    assert len(set(scores.values())) == 1, scores
