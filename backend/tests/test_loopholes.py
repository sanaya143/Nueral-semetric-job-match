"""Regression tests for loopholes found in the version 1 review.

1. One action-verb sentence stuffed with skills must not make every skill "demonstrated".
2. Invisible, near-transparent, very light or covered PDF text (and near-white / table text in DOCX)
   must be treated as hidden, never as evidence.
7. No login in this build, so other websites must not be able to write through the browser (CSRF),
   and foreign Host headers (DNS rebinding) must be refused.
Plus: the candidate list filter searches evidence and skills, not only the headline.
"""

from __future__ import annotations

import io

import docx
import pymupdf
from docx.shared import RGBColor
from fastapi.testclient import TestClient

from nsm.api.app import app
from nsm.extraction.heuristic import extract
from nsm.ingestion.parsing import parse_docx, parse_pdf
from nsm.scoring.scorer import score_candidate
from nsm.scoring.types import AtomSpec, CandidateView, JobSpec, UnitView

STUFFED = """Experience
Senior AI Engineer, Acme Lending (2018 - present)
- Built and shipped to production a RAG pipeline in Python using OpenAI API, LangChain, LangGraph, pgvector, Qdrant, Docker, AWS and LLM evaluation, cutting costs 40%.
"""

HONEST = """Experience
AI Engineer, Acme Lending (2018 - present)
- Built a RAG pipeline in Python with pgvector for the operations team.
"""


# --- 1. skill stuffing -----------------------------------------------------------------

def test_skill_dense_sentence_is_not_demonstrated():
    res = extract(STUFFED)
    levels = {c.skill_id: c.level for c in res.claims if c.via == "direct"}
    assert levels, "skills should still be found"
    assert "demonstrated" not in levels.values()
    assert "skill_dense_sentences" in res.flags


def test_normal_sentence_still_demonstrated():
    res = extract(HONEST)
    levels = {c.skill_id: c.level for c in res.claims if c.via == "direct"}
    assert levels.get("rag") == "demonstrated" and levels.get("python") == "demonstrated"
    assert "skill_dense_sentences" not in res.flags


def _verdict(atom_id: str, quote: str) -> dict:
    return {"atom_id": atom_id, "atom_text": atom_id, "kind": "must", "dimension": "technical",
            "level": "demonstrated", "status": "met", "score": 1.0, "confidence": 0.9, "note": "",
            "evidence": [{"unit_id": "u1", "quote": quote, "context": "", "level": "demonstrated",
                          "skill_id": atom_id, "via": "direct"}]}


def test_one_quote_backing_many_requirements_scores_lower():
    atoms = [AtomSpec(f"a{i}", f"skill {i}", "must", "technical", [f"s{i}"], "all", None, 1.0) for i in range(5)]
    job = JobSpec("Job", atoms, None, None, None)
    cv = CandidateView(id="c", headline="", total_years=5, location=None, work_mode=None, notice_period_days=None,
                       flags=[], extraction_mode="heuristic",
                       units={"u1": UnitView(id="u1", kind="experience", title="x", context_header="", text="x",
                                             start_year=2022, end_year=None)}, claims={})
    shared = score_candidate(job, cv, [_verdict(a.id, "one sentence") for a in atoms])
    distinct = score_candidate(job, cv, [_verdict(a.id, f"sentence {i}") for i, a in enumerate(atoms)])
    assert shared["score"] < distinct["score"]
    assert shared["confidence"] < distinct["confidence"]
    assert any("rest on one sentence" in c["text"] for c in shared["concerns"])


# --- 2. hidden text --------------------------------------------------------------------

def _pdf(build) -> bytes:  # noqa: ANN001
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Wrote Python scripts for reporting.", fontsize=11)
    build(page)
    return doc.tobytes()


def test_pdf_invisible_render_mode_is_hidden():
    p = parse_pdf(_pdf(lambda pg: pg.insert_text((72, 90), "Built RAG pipelines", fontsize=11, render_mode=3)))
    assert "Built RAG" not in p.text and "Built RAG pipelines" in p.hidden_snippets


def test_pdf_light_grey_is_hidden_but_normal_grey_is_kept():
    p = parse_pdf(_pdf(lambda pg: (pg.insert_text((72, 90), "Pale claim", fontsize=11, color=(0.88, 0.88, 0.88)),
                                   pg.insert_text((72, 108), "Grey dates 2020", fontsize=11, color=(0.5, 0.5, 0.5)))))
    assert "Pale claim" not in p.text and "Pale claim" in p.hidden_snippets
    assert "Grey dates 2020" in p.text


def test_pdf_transparent_text_is_hidden():
    p = parse_pdf(_pdf(lambda pg: pg.insert_text((72, 90), "Ghost claim", fontsize=11, fill_opacity=0.05)))
    assert "Ghost claim" not in p.text


def test_pdf_text_covered_by_box_is_hidden():
    def build(pg):  # noqa: ANN001, ANN202
        pg.insert_text((72, 90), "Covered claim", fontsize=11)
        pg.draw_rect(pymupdf.Rect(60, 75, 300, 95), color=None, fill=(1, 1, 1))
    p = parse_pdf(_pdf(build))
    assert "Covered claim" not in p.text
    assert "Wrote Python scripts" in p.text


def test_pdf_text_on_coloured_panel_is_kept():
    def build(pg):  # noqa: ANN001, ANN202
        pg.draw_rect(pymupdf.Rect(60, 75, 300, 95), color=None, fill=(0.9, 0.9, 1.0))  # drawn first
        pg.insert_text((72, 90), "Sidebar skill Python", fontsize=11)
    assert "Sidebar skill Python" in parse_pdf(_pdf(build)).text


def test_docx_near_white_and_table_text_is_hidden():
    d = docx.Document()
    d.add_paragraph("Wrote Python scripts for reporting.")
    d.add_paragraph().add_run("Near white claim").font.color.rgb = RGBColor(0xFE, 0xFE, 0xFE)
    cell = d.add_table(rows=1, cols=1).rows[0].cells[0].paragraphs[0]
    cell.add_run("Visible cell")
    cell.add_run(" white cell claim").font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    b = io.BytesIO()
    d.save(b)
    p = parse_docx(b.getvalue())
    assert "Near white claim" not in p.text and "white cell claim" not in p.text
    assert "Visible cell" in p.text
    assert {"Near white claim", "white cell claim"} <= set(p.hidden_snippets)


# --- 7. browser-based attacks on the local API -----------------------------------------

H = {"X-Tenant-ID": "t-guard"}
CV = b"Asha Nair\nExperience\nEngineer, Q Ltd (2019 - present)\n- Built data pipelines in Python and Spark.\n"


def test_cross_site_write_is_refused():
    with TestClient(app) as c:
        r = c.post("/api/v1/candidates/upload", headers={**H, "Origin": "http://evil.example"},
                   files=[("files", ("x.txt", CV, "text/plain"))])
        assert r.status_code == 403
        r = c.post("/api/v1/candidates/upload", headers={**H, "Sec-Fetch-Site": "cross-site"},
                   files=[("files", ("x.txt", CV, "text/plain"))])
        assert r.status_code == 403
        assert c.get("/api/v1/candidates", headers=H).json()["total"] == 0


def test_same_origin_and_dev_origin_writes_are_allowed():
    with TestClient(app) as c:
        for origin in ("http://testserver", "http://localhost:5173"):
            r = c.post("/api/v1/feedback", headers={**H, "Origin": origin},
                       json={"run_id": "nope", "candidate_id": "nope", "action": "maybe"})
            assert r.status_code == 404  # reached the endpoint (run not found), not blocked


def test_foreign_host_header_is_refused():
    with TestClient(app) as c:
        assert c.get("/api/v1/health", headers={"Host": "attacker.example"}).status_code == 403
        assert c.get("/api/v1/health", headers={"Host": "localhost:8000"}).status_code == 200


# --- candidate list filter -------------------------------------------------------------

def test_candidate_filter_searches_evidence_not_only_headline():
    with TestClient(app) as c:
        h = {"X-Tenant-ID": "t-filter"}
        c.post("/api/v1/candidates/upload", headers=h, files=[("files", ("asha.txt", CV, "text/plain"))])
        assert c.get("/api/v1/candidates", headers=h, params={"q": "spark"}).json()["total"] == 1
        assert c.get("/api/v1/candidates", headers=h, params={"q": "spark pipelines"}).json()["total"] == 1
        assert c.get("/api/v1/candidates", headers=h, params={"q": "asha.txt"}).json()["total"] == 1
        assert c.get("/api/v1/candidates", headers=h, params={"q": "kubernetes"}).json()["total"] == 0
        # names are not searchable, so blind review holds
        assert c.get("/api/v1/candidates", headers=h, params={"q": "Nair"}).json()["total"] == 0


# --- no built-in model ------------------------------------------------------------------

def test_no_model_is_built_in():
    from nsm.config import Settings
    s = Settings(_env_file=None, llm_mode="openrouter", openrouter_api_key="k", model_small="", model_large="",
                 **{f"model_{t}": "" for t in ("extract", "escalate", "jd", "verify", "deep", "report", "search")})
    assert not s.llm_enabled and s.missing_models  # openrouter mode stays off until models are chosen
    s2 = Settings(_env_file=None, llm_mode="openrouter", openrouter_api_key="k", model_small="a/small",
                  model_large="b/large", model_deep="c/deep")
    assert s2.llm_enabled
    assert s2.model_for_task("extract") == "a/small" and s2.model_for_task("jd") == "b/large"
    assert s2.model_for_task("deep") == "c/deep"


def test_config_endpoint_works_without_models():
    with TestClient(app) as c:
        r = c.get("/api/v1/config")
        assert r.status_code == 200 and "models" in r.json()
