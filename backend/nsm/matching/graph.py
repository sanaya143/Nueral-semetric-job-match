"""Match-run graph (LangGraph, ADR 0008): deterministic edges, model calls only via the gateway.

  filter -> retrieve -> rerank -> verify -> score -> deep_eval -> END
               \\________ (nobody retrieved) ______________________/

Stage sizes come from settings (D-011): 300 -> 50 -> deep eval top 5 (+ lazy 6-10).
Every node is a plain function over the state, so each stage is testable on its own.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from nsm.config import Settings
from nsm.gateway import BudgetExceeded, Gateway, GatewayUnavailable
from nsm.matching.deep_eval import llm_deep_eval, offline_deep_eval
from nsm.models import Candidate
from nsm.retrieval.hybrid import retrieve
from nsm.scoring.scorer import score_candidate, sort_key
from nsm.scoring.types import JobSpec, load_candidates
from nsm.scoring.verdicts import llm_verify, offline_verdicts


class RunState(TypedDict, total=False):
    filters: dict
    verify: bool
    allowed: set
    stage1: list  # [(cid, score)]
    semantic: dict
    shortlist: list  # [cid]
    cvs: dict
    verdicts: dict  # cid -> list[dict]
    results: list  # list[dict], ranked
    stats: dict


def _city_norm(s: str | None) -> str:
    from nsm.scoring.verdicts import _city
    return _city(s) or ""


def stage0_allowed(session: Session, tenant_id: str, filters: dict | None) -> tuple[set[str], int]:
    """Recruiter-set hard filters only. Unknown values are kept (never filtered on silence)."""
    f = filters or {}
    rows = session.execute(select(Candidate.id, Candidate.location, Candidate.work_mode, Candidate.notice_period_days)
                           .where(Candidate.tenant_id == tenant_id, Candidate.status == "ready")).all()
    allowed: set[str] = set()
    for cid, loc, mode, notice in rows:
        if f.get("location") and loc and _city_norm(f["location"]) not in _city_norm(loc) \
                and _city_norm(loc) not in _city_norm(f["location"]):
            continue
        if f.get("work_mode") and mode and f["work_mode"] != "any" and mode != f["work_mode"] and \
                not (f["work_mode"] == "hybrid" and mode == "onsite"):
            continue
        if f.get("max_notice_days") is not None and notice is not None and notice > int(f["max_notice_days"]):
            continue
        allowed.add(cid)
    return allowed, len(rows)


def build_graph(session: Session, gateway: Gateway, settings: Settings, tenant_id: str, job: JobSpec,
                run_id: str | None):
    def t(stats: dict, key: str, t0: float) -> None:
        stats.setdefault("timings_ms", {})[key] = int((time.perf_counter() - t0) * 1000)

    def filter_node(state: RunState) -> dict[str, Any]:
        t0 = time.perf_counter()
        allowed, total = stage0_allowed(session, tenant_id, state.get("filters"))
        stats = dict(state.get("stats") or {})
        stats.update(pool=total, after_filters=len(allowed))
        t(stats, "stage0", t0)
        return {"allowed": allowed, "stats": stats}

    def retrieve_node(state: RunState) -> dict[str, Any]:
        t0 = time.perf_counter()
        s1 = retrieve(session, gateway, tenant_id, job.atoms, state["allowed"], settings.stage1_top_k, run_id)
        stats = dict(state["stats"])
        stats.update(stage1=len(s1.ranked), stage1_detail=s1.stats)
        t(stats, "stage1", t0)
        return {"stage1": s1.ranked, "semantic": s1.semantic_hits, "stats": stats}

    def rerank_node(state: RunState) -> dict[str, Any]:
        """Stage 2: cheap, deterministic evidence rerank (+ optional cross-encoder)."""
        t0 = time.perf_counter()
        s1 = dict(state["stage1"])
        cvs = load_candidates(session, tenant_id, list(s1))
        verdicts, prelim = {}, []
        for cid, cv in cvs.items():
            v = offline_verdicts(job, cv, state["semantic"].get(cid, {}))
            sc = score_candidate(job, cv, v)
            verdicts[cid] = v
            prelim.append((cid, sc, 0.75 * sc["score"] / 100 + 0.25 * s1.get(cid, 0.0)))
        rerank_used = False
        if gateway.rerank_enabled and prelim:
            musts = "; ".join(a.text for a in job.atoms if a.kind == "must")
            docs = [" | ".join(e["quote"] for vv in verdicts[cid] for e in vv["evidence"] if e.get("quote"))[:1500]
                    or cvs[cid].headline for cid, _, _ in prelim]
            res = gateway.rerank(session, musts, docs, tenant_id=tenant_id, top_n=len(docs), run_id=run_id)
            if res:
                rr = {i: s for i, s in res}
                prelim = [(cid, sc, 0.7 * comb + 0.3 * rr.get(i, 0.0)) for i, (cid, sc, comb) in enumerate(prelim)]
                rerank_used = True
        prelim.sort(key=lambda x: (not x[1]["gate_passed"], -x[2]))
        shortlist = [cid for cid, _, _ in prelim[: settings.stage2_top_k]]
        stats = dict(state["stats"])
        stats.update(stage2=len(shortlist), cross_encoder=rerank_used)
        t(stats, "stage2", t0)
        return {"shortlist": shortlist, "cvs": {c: cvs[c] for c in shortlist},
                "verdicts": {c: verdicts[c] for c in shortlist}, "stats": stats}

    def verify_node(state: RunState) -> dict[str, Any]:
        t0 = time.perf_counter()
        stats = dict(state["stats"])
        info = {"attempted": 0, "verified": 0, "skipped": 0, "rejected_citations": 0, "errors": 0, "budget_stop": None}
        verdicts = dict(state["verdicts"])
        if state.get("verify", True) and gateway.llm_enabled:
            consecutive_errors = 0
            for i, cid in enumerate(state["shortlist"]):
                info["attempted"] += 1
                try:
                    v, vi = llm_verify(session, gateway, tenant_id, run_id, job, state["cvs"][cid], verdicts[cid],
                                       state["semantic"].get(cid, {}))
                    verdicts[cid] = v
                    info["verified"] += 1
                    info["skipped"] += 1 if vi.get("skipped") else 0  # every atom already LLM-evidenced: no call
                    info["rejected_citations"] += vi["rejected_citations"]
                    consecutive_errors = 0
                except BudgetExceeded as e:
                    info["budget_stop"] = f"stopped at candidate {i + 1}: {e}"
                    break
                except GatewayUnavailable:
                    info["errors"] += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        info["budget_stop"] = "stopped after 3 consecutive provider errors"
                        break
        stats["verification"] = info
        t(stats, "stage3", t0)
        return {"verdicts": verdicts, "stats": stats}

    def score_node(state: RunState) -> dict[str, Any]:
        t0 = time.perf_counter()
        s1 = dict(state["stage1"])
        results = []
        for cid in state["shortlist"]:
            v = state["verdicts"][cid]
            sc = score_candidate(job, state["cvs"][cid], v)
            verifier = "llm" if any(x.get("verified_by") == "llm" for x in v) else "offline"
            results.append({"candidate_id": cid, "verdicts": v, "stage1_score": s1.get(cid, 0.0),
                            "verifier": verifier, **sc})
        results.sort(key=sort_key)
        for i, r in enumerate(results):
            r["rank"] = i + 1
        stats = dict(state["stats"])
        t(stats, "score", t0)
        return {"results": results, "stats": stats}

    def deep_eval_node(state: RunState) -> dict[str, Any]:
        t0 = time.perf_counter()
        stats = dict(state["stats"])
        info = {"llm": 0, "offline": 0, "budget_stop": None}
        results = state["results"]
        for r in results:
            cv = state["cvs"][r["candidate_id"]]
            r["deep_eval"] = offline_deep_eval(job, cv, r["verdicts"], r)
            info["offline"] += 1
        if gateway.llm_enabled and state.get("verify", True):
            for r in results[: settings.deep_eval_eager_k]:
                try:
                    r["deep_eval"] = llm_deep_eval(session, gateway, tenant_id, run_id, job,
                                                   state["cvs"][r["candidate_id"]], r["verdicts"], r)
                    info["llm"] += 1
                except BudgetExceeded as e:
                    info["budget_stop"] = str(e)
                    break
                except GatewayUnavailable:
                    continue
        stats["deep_eval"] = info
        t(stats, "stage4", t0)
        return {"results": results, "stats": stats}

    g = StateGraph(RunState)
    g.add_node("filter", filter_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("rerank", rerank_node)
    g.add_node("verify", verify_node)
    g.add_node("score", score_node)
    g.add_node("deep_eval", deep_eval_node)
    g.add_edge(START, "filter")
    g.add_edge("filter", "retrieve")
    g.add_conditional_edges("retrieve", lambda s: "rerank" if s["stage1"] else END, ["rerank", END])
    g.add_edge("rerank", "verify")
    g.add_edge("verify", "score")
    g.add_edge("score", "deep_eval")
    g.add_edge("deep_eval", END)
    return g.compile()
