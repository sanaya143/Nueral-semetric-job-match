"""Model Gateway (budgets, cost meter, PII scrub) and the LLM paths, with a fake client."""

from __future__ import annotations

import pytest
from conftest import CV_B, JD_AI
from sqlalchemy import select

from nsm.db import get_scoped, session_scope
from nsm.extraction.llm import extract_with_llm
from nsm.gateway import BudgetExceeded, Gateway, GatewayUnavailable
from nsm.gateway.pricing import cost_usd
from nsm.ingestion.pii import redact
from nsm.ingestion.pipeline import ingest_bytes
from nsm.ingestion.sanitize import sanitize
from nsm.jobs.service import confirm_job, create_job
from nsm.matching.service import job_spec
from nsm.models import Job, LlmCall
from nsm.scoring.types import load_candidates
from nsm.scoring.verdicts import llm_verify, offline_verdicts

SCHEMA = {"type": "object"}


def test_offline_mode_never_calls_a_model():
    with session_scope() as s, pytest.raises(GatewayUnavailable):
        Gateway().chat_json(s, tenant_id="t", task="verify", system="x", user="y", schema=SCHEMA, schema_name="x")


def test_prices_and_batch_discount():
    assert cost_usd("anthropic/claude-haiku-5.5", 1_000_000, 0) == pytest.approx(0.10)
    assert cost_usd("anthropic/claude-haiku-5.5:batch", 1_000_000, 1_000_000) == pytest.approx(0.30)
    assert cost_usd("unknown/model", 1_000_000, 0) == pytest.approx(5.0)  # conservative for unknown models


def test_cost_meter_records_every_call_with_fee(llm_gateway):
    gw = llm_gateway([{"ok": 1}], input_tokens=10_000, output_tokens=1_000)
    with session_scope() as s:
        gw.chat_json(s, tenant_id="t-meter", task="verify", system="sys", user="u", schema=SCHEMA,
                     schema_name="x", run_id="run-meter")
        row = s.scalars(select(LlmCall).where(LlmCall.run_id == "run-meter")).one()
        expected = cost_usd("anthropic/claude-haiku-5.5", 10_000, 1_000) * 1.055
        assert row.cost_usd == pytest.approx(expected) and row.input_tokens == 10_000 and row.ok


def test_per_run_cap_blocks_before_the_call(llm_gateway):
    gw = llm_gateway([{"ok": 1}] * 50, input_tokens=50_000, output_tokens=5_000, cap_per_run_usd=0.02)
    calls = 0
    with session_scope() as s, pytest.raises(BudgetExceeded):
        for _ in range(50):
            gw.chat_json(s, tenant_id="t-cap", task="deep", system="s", user="u", schema=SCHEMA, schema_name="x",
                         run_id="run-cap")
            calls += 1
    assert 0 < calls < 50
    assert len(gw._client.calls) == calls  # the blocked call never reached the provider


def test_thinking_is_off_and_a_refusal_falls_back_to_low_effort(llm_gateway):
    from nsm.gateway import MAX_TOKENS, THINKING_RESERVE
    from nsm.gateway.clients import ThinkingRequired
    refusal = ThinkingRequired('OpenRouter HTTP 400: "thinking.type.disabled" is not supported for this model')
    gw = llm_gateway([{"ok": 1}, refusal, {"ok": 2}, {"ok": 3}])
    with session_scope() as s:
        gw.chat_json(s, tenant_id="t-think", task="verify", system="s", user="u", schema=SCHEMA, schema_name="x")
        assert gw.chat_json(s, tenant_id="t-think", task="deep", system="s", user="u", schema=SCHEMA,
                            schema_name="x") == {"ok": 2}
        gw.chat_json(s, tenant_id="t-think", task="deep", system="s", user="u", schema=SCHEMA, schema_name="x")
        n_meter = len(s.scalars(select(LlmCall).where(LlmCall.tenant_id == "t-think")).all())
    verify, refused, retry, later = gw._client.calls
    assert verify["effort"] is None and refused["effort"] is None            # off by default, every task
    assert retry["effort"] == later["effort"] == "low"                        # remembered per model
    assert retry["max_tokens"] == MAX_TOKENS["deep"] + THINKING_RESERVE       # room for the JSON after thinking
    assert n_meter == 3                                                       # the free 400 is not metered


def test_openrouter_body_switches_reasoning_off(monkeypatch):
    from nsm.gateway.clients import OpenRouterClient
    sent = {}

    def fake_post(self, path, body):
        sent.update(body)
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}, 1
    monkeypatch.setattr(OpenRouterClient, "_post", fake_post)
    OpenRouterClient("k", "http://x").chat_json("anthropic/claude-haiku-5.5", "s", "u", SCHEMA, "x", 100, None)
    assert sent["reasoning"] == {"enabled": False}


def test_monthly_cap(llm_gateway):
    gw = llm_gateway([{"ok": 1}], cap_monthly_usd=0.0)
    with session_scope() as s, pytest.raises(BudgetExceeded):
        gw.chat_json(s, tenant_id="t-month", task="verify", system="s", user="u", schema=SCHEMA, schema_name="x")
    assert gw._client.calls == []


def test_prompts_are_scrubbed_of_contact_details(llm_gateway):
    gw = llm_gateway([{"ok": 1}])
    with session_scope() as s:
        gw.chat_json(s, tenant_id="t-scrub", task="verify", system="s", schema=SCHEMA, schema_name="x",
                     user="mail me at someone@example.com or +91 98765 43210, see github.com/someone")
    sent = gw._client.calls[0]["user"]
    assert "someone@example.com" not in sent and "98765" not in sent and "github.com/someone" not in sent


def test_llm_extraction_keeps_validated_quotes_and_drops_invented_ones(llm_gateway):
    text = redact(sanitize(CV_B).text).text

    def respond(user):
        return {"evidence": [
            {"unit": "u0", "quote": "Implemented a document retrieval pipeline with chunking and pgvector",
             "level": "demonstrated", "skills": ["semantic_search", "vector_db"]},  # one row, two skills
            {"unit": "u0", "quote": "Built an internal document Q&A assistant using the OpenAI API",
             "level": "demonstrated", "skills": ["llm_app_dev"]},
            {"unit": "u0", "quote": "Deployed the assistant to production on AWS ECS using Docker",
             "level": "applied", "skills": ["docker"]},
            {"unit": "u1", "quote": "Designed REST APIs for a payments reconciliation platform",
             "level": "demonstrated", "skills": ["rest_api"]},
            {"unit": "u0", "quote": "Ran the platform on Kubernetes with 40 services",
             "level": "demonstrated", "skills": ["kubernetes"]},  # invented: 1 of 5 rejected
        ], "unmapped_terms": ["Q&A assistant"]}

    gw = llm_gateway([respond])
    with session_scope() as s:
        res = extract_with_llm(s, gw, text, tenant_id="t-llmx")
    skills = {(c.skill_id, c.level, c.via) for c in res.claims}
    assert ("semantic_search", "demonstrated", "llm") in skills and ("vector_db", "demonstrated", "llm") in skills
    assert not any(sid == "kubernetes" for sid, _, _ in skills)
    assert res.mode == "llm" and any(f.startswith("llm_quotes_rejected") for f in res.flags)
    assert ("rag", "demonstrated", "direct") in skills  # deterministic claims still there


def test_extract_prompt_sends_only_units_that_can_describe_work(llm_gateway):
    gw = llm_gateway([{"evidence": [], "unmapped_terms": []}])
    with session_scope() as s:
        res = extract_with_llm(s, gw, redact(sanitize(CV_B).text).text, tenant_id="t-units")
    sent = gw._client.calls[0]["user"]
    assert 'kind="experience"' in sent
    assert 'kind="education"' not in sent and 'kind="preferences"' not in sent and "B.Tech" not in sent
    assert res.mode == "llm"


def test_extraction_escalates_only_the_units_that_failed(llm_gateway):
    bad = {"evidence": [{"unit": "u0", "quote": "made up text here", "level": "demonstrated",
                         "skills": ["python"]}] * 3, "unmapped_terms": []}
    good = {"evidence": [], "unmapped_terms": []}
    gw = llm_gateway([bad, good])
    with session_scope() as s:
        res = extract_with_llm(s, gw, redact(sanitize(CV_B).text).text, tenant_id="t-esc")
    first, second = gw._client.calls
    assert [first["model"], second["model"]] == ["anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"]
    assert res.mode == "llm_escalated"
    assert '<unit id="u0"' in second["user"] and '<unit id="u1"' not in second["user"]  # only the failed unit
    assert '<unit id="u1"' in first["user"]


_MOSTLY_BAD = {"evidence": [
    {"unit": "u0", "quote": "Built an internal document Q&A assistant using the OpenAI API",
     "level": "demonstrated", "skills": ["llm_app_dev"]},                                  # valid
    *[{"unit": "u0", "quote": "made up text here", "level": "demonstrated", "skills": ["python"]}] * 3,
], "unmapped_terms": []}


def test_escalation_fits_under_the_per_cv_hard_cap(llm_gateway):
    """With a candidate id the per-CV cap is checked; extract + a worst-case Sonnet escalation must fit."""
    gw = llm_gateway([_MOSTLY_BAD, {"evidence": [], "unmapped_terms": []}], input_tokens=1500, output_tokens=1500)
    with session_scope() as s:
        res = extract_with_llm(s, gw, redact(sanitize(CV_B).text).text, tenant_id="t-cap", candidate_id="cand-cap-1")
    assert [c["model"] for c in gw._client.calls] == ["anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"]
    assert res.mode == "llm_escalated"


def test_blocked_escalation_keeps_small_model_claims_and_labels_them(llm_gateway):
    gw = llm_gateway([_MOSTLY_BAD], input_tokens=1500, output_tokens=1500, cap_per_cv_hard_usd=0.005)
    with session_scope() as s:
        res = extract_with_llm(s, gw, redact(sanitize(CV_B).text).text, tenant_id="t-cap", candidate_id="cand-cap-2")
    assert [c["model"] for c in gw._client.calls] == ["anthropic/claude-haiku-5.5"]   # Sonnet blocked before the call
    assert res.mode == "llm"
    assert any(f.startswith("llm_escalation_unavailable:per-CV hard cap") for f in res.flags)
    assert any(c.skill_id == "llm_app_dev" and c.via == "llm" for c in res.claims)


def test_verifier_rejects_uncited_upgrades_and_cannot_erase_evidence(llm_gateway):
    t = "t-verify"
    cid, _ = ingest_bytes(t, "b.txt", CV_B.encode())
    with session_scope() as s:
        from nsm.gateway import get_gateway
        job, _ = create_job(s, get_gateway(), t, "AI Engineer", JD_AI)
        confirm_job(s, job)
        spec = job_spec(get_scoped(s, Job, t, job.id))
        cv = load_candidates(s, t, [cid])[cid]
        offline = offline_verdicts(spec, cv)
        idx_rag = next(i for i, a in enumerate(spec.atoms) if "RAG" in a.text)
        idx_py = next(i for i, a in enumerate(spec.atoms) if a.text == "Strong Python")
        gw = llm_gateway([{"verdicts": [
            {"atom": str(idx_rag), "level": "missing", "unit": None, "quote": None},  # tries to erase evidence
            {"atom": str(idx_py), "level": "demonstrated", "unit": "nope", "quote": "x" * 20},  # bad unit
        ]}])
        verified, info = llm_verify(s, gw, t, None, spec, cv, offline, {})
    assert verified[idx_rag]["level"] == offline[idx_rag]["level"] == "demonstrated"
    assert verified[idx_py] == offline[idx_py]
    assert info["rejected_citations"] == 2 and info["accepted"] == 0


def _spec_cv_offline(t):
    cid, _ = ingest_bytes(t, "b.txt", CV_B.encode())
    with session_scope() as s:
        from nsm.gateway import get_gateway
        job, _ = create_job(s, get_gateway(), t, "AI Engineer", JD_AI)
        confirm_job(s, job)
        spec = job_spec(get_scoped(s, Job, t, job.id))
        cv = load_candidates(s, t, [cid])[cid]
    return spec, cv, offline_verdicts(spec, cv)


def test_verifier_only_asks_about_requirements_it_can_still_change(llm_gateway):
    spec, cv, offline = _spec_cv_offline("t-vskip")
    rubric = [i for i, a in enumerate(spec.atoms) if a.dimension != "logistics" and not
              (a.dimension == "experience" and a.min_years)]
    done, uid = [dict(v) for v in offline], next(iter(cv.units))
    for i in rubric:  # every requirement already demonstrated by a validated LLM claim
        done[i] = {**done[i], "level": "demonstrated", "evidence": [{"unit_id": uid, "quote": "q" * 20, "via": "llm"}]}
    gw = llm_gateway([{"verdicts": []}])
    with session_scope() as s:
        _, info = llm_verify(s, gw, "t-vskip", None, spec, cv, done, {})
        assert info["skipped"] and gw._client.calls == []          # nothing left to judge: no call at all
        open_one = [dict(v) for v in done]
        open_one[rubric[0]] = offline[rubric[0]] if offline[rubric[0]]["level"] != "demonstrated" else \
            {**offline[rubric[0]], "level": "missing", "evidence": []}
        llm_verify(s, gw, "t-vskip", None, spec, cv, open_one, {})
    assert f"Judge only these requirement ids: {rubric[0]}\n" in gw._client.calls[0]["user"]


def test_deep_eval_strengths_cite_validated_evidence_by_ref(llm_gateway):
    from nsm.matching.deep_eval import deep_user_prompt, llm_deep_eval
    spec, cv, offline = _spec_cv_offline("t-deep")
    scored = {"score": 80.0, "gate_passed": True}
    user, refs = deep_user_prompt(spec, cv, offline, scored)
    assert refs and "- E1 [" in user
    gw = llm_gateway([{"summary": "ok", "strengths": [{"text": "a", "ref": "E1"}, {"text": "b", "ref": "E99"}],
                       "gaps": [], "interview_questions": []}])
    with session_scope() as s:
        de = llm_deep_eval(s, gw, "t-deep", None, spec, cv, offline, scored)
    assert [x["quote"] for x in de["strengths"]] == [refs["E1"]["quote"]]   # quote comes from the evidence
    assert de["dropped_uncited_strengths"] == 1                            # an unknown ref is dropped
