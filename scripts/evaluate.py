"""Evaluate the engine against keyword baselines on the synthetic gold set.

    backend/.venv/Scripts/python.exe -X utf8 scripts/evaluate.py            # offline ($0)
    backend/.venv/Scripts/python.exe -X utf8 scripts/evaluate.py --use-env  # mode from .env (may spend)

Runs in a throw-away data folder (never touches data/nsm.db). Writes
docs/eval/initial-synthetic-eval.md and .json.

Systems compared on every JD:
  engine      the full funnel (hybrid retrieval -> evidence rerank -> verdicts -> deterministic score)
  keyword_ats keyword frequency over the raw parsed text (incl. hidden text a naive parser keeps), title bonus
  bm25        BM25 over the whole (sanitised, redacted) CV text
Metrics: nDCG@10 (gain 2^g-1), P@5 (grade>=2), Recall@20 (grade>=2), keyword-stuffers in top 10,
rank of the "transferable" candidate (different title, relevant described work), and fairness invariance.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "synthetic"
OUT = ROOT / "docs" / "eval"


def setup_env(use_env: bool) -> None:
    os.environ["NSM_DATA_DIR"] = tempfile.mkdtemp(prefix="nsm-eval-")
    if not use_env:
        os.environ.update(NSM_LLM_MODE="offline", NSM_EMBEDDING_MODE="local", NSM_RERANK_MODE="none")


def ndcg(grades_in_rank_order: list[int], all_grades: list[int], k: int = 10) -> float:
    def dcg(gs: list[int]) -> float:
        return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gs[:k]))
    ideal = dcg(sorted(all_grades, reverse=True))
    return dcg(grades_in_rank_order) / ideal if ideal > 0 else 0.0


def p_at(grades: list[int], k: int = 5) -> float:
    return sum(1 for g in grades[:k] if g >= 2) / k


def recall_at(grades: list[int], all_grades: list[int], k: int = 20) -> float:
    rel = sum(1 for g in all_grades if g >= 2)
    return sum(1 for g in grades[:k] if g >= 2) / rel if rel else 0.0


_TOK = re.compile(r"[a-z0-9][a-z0-9+#.]*")


def bm25_rank(query: str, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> list[str]:
    toks = {d: [t.strip(".") for t in _TOK.findall(x.lower())] for d, x in docs.items()}
    n = len(docs)
    avg = sum(len(t) for t in toks.values()) / max(1, n)
    df: Counter = Counter()
    for t in toks.values():
        df.update(set(t))
    q = [t.strip(".") for t in _TOK.findall(query.lower())]
    scores = {}
    for d, t in toks.items():
        tf = Counter(t)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(t) / avg))
        scores[d] = s
    return sorted(docs, key=lambda d: -scores[d])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-env", action="store_true", help="use the mode from .env (OpenRouter may spend money)")
    args = ap.parse_args()
    if not (DATA / "manifest.json").exists():
        print("run scripts/generate_synthetic.py first", file=sys.stderr)
        return 1
    setup_env(args.use_env)

    from sqlalchemy import func, select

    from nsm.baseline.keyword import score as kw_score
    from nsm.db import init_db, session_scope
    from nsm.gateway import get_gateway
    from nsm.ingestion.parsing import parse_document
    from nsm.ingestion.pipeline import ingest_bytes
    from nsm.jobs.service import confirm_job, create_job
    from nsm.matching.service import run_match_sync
    from nsm.models import Candidate, Document, LlmCall, MatchResult, MatchRun

    init_db()
    gw = get_gateway()
    mode = "openrouter" if gw.llm_enabled else "offline"
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    persona = {c["file"]: c["persona"] for c in manifest["cvs"]}
    archetype = {c["file"]: c.get("archetype") for c in manifest["cvs"]}
    gold: dict[str, dict[str, int]] = {}
    for r in csv.DictReader(open(DATA / "gold" / "labels.csv", encoding="utf-8")):
        if r["persona"] == "duplicate":
            continue  # dedup is a feature: the re-sent copy is not a separate candidate
        gold.setdefault(r["jd_id"], {})[r["cv_file"]] = int(r["grade"])

    tenant = "eval"
    files = sorted(f for f in (DATA / "cvs").iterdir() if "_resent" not in f.name)
    t0 = time.perf_counter()
    cid_to_file: dict[str, str] = {}
    raw_text: dict[str, str] = {}
    for f in files:
        data = f.read_bytes()
        cid, status = ingest_bytes(tenant, f.name, data, source="synthetic")
        cid_to_file[cid] = f.name
        pd = parse_document(f.name, data)
        raw_text[f.name] = pd.text + "\n" + "\n".join(pd.hidden_snippets)  # naive parser keeps hidden text
    ingest_s = time.perf_counter() - t0
    with session_scope() as s:
        evaluable = {cid_to_file[d.candidate_id]: d.evaluable_text for d in s.scalars(
            select(Document).where(Document.tenant_id == tenant))}

    per_jd = []
    for jid, jd_meta in manifest["jds"].items():
        title, _, body = (DATA / "jds" / jd_meta["file"]).read_text(encoding="utf-8").partition("\n")
        with session_scope() as s:
            job, _ = create_job(s, gw, tenant, title.strip(), body.strip())
            confirm_job(s, job)
            atoms = [a.text for a in job.atoms]
            job_id = job.id
        t1 = time.perf_counter()
        rid = run_match_sync(tenant, job_id)
        run_s = time.perf_counter() - t1
        with session_scope() as s:
            rows = s.scalars(select(MatchResult).where(MatchResult.run_id == rid).order_by(MatchResult.rank)).all()
            engine_order = [cid_to_file[r.candidate_id] for r in rows]
            run_cost = float(s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
                                      .where(LlmCall.run_id == rid)) or 0.0)
        rest = [f for f in gold[jid] if f not in engine_order]
        engine_order += rest
        kw_order = sorted(gold[jid], key=lambda f: -kw_score(raw_text[f], title.strip(), atoms))
        bm_order = bm25_rank(title + " " + " ".join(atoms), {f: evaluable[f] for f in gold[jid]})
        all_g = list(gold[jid].values())
        own_arch = jd_meta["archetype"]
        res = {"jd": jid, "title": title.strip(), "relevant": sum(1 for g in all_g if g >= 2),
               "run_seconds": round(run_s, 3), "run_cost_usd": round(run_cost, 6), "systems": {}}
        for name, order in (("engine", engine_order), ("keyword_ats", kw_order), ("bm25", bm_order)):
            gs = [gold[jid][f] for f in order]
            stuffers_top10 = sum(1 for f in order[:10] if persona.get(f) == "stuffer")
            transferable = next((i + 1 for i, f in enumerate(order)
                                 if persona.get(f) == "transferable" and archetype.get(f) == own_arch), None)
            own_stuffer = next((i + 1 for i, f in enumerate(order)
                                if persona.get(f) == "stuffer" and archetype.get(f) == own_arch), None)
            res["systems"][name] = {"ndcg@10": round(ndcg(gs, all_g), 3), "p@5": round(p_at(gs), 3),
                                    "recall@20": round(recall_at(gs, all_g), 3), "stuffers_top10": stuffers_top10,
                                    "transferable_rank": transferable, "own_stuffer_rank": own_stuffer}
        per_jd.append(res)
        print(f"{jid:20} " + " | ".join(f"{k}: nDCG {v['ndcg@10']:.2f} P@5 {v['p@5']:.2f} R@20 {v['recall@20']:.2f}"
                                       for k, v in res["systems"].items()))

    # fairness: counterfactual pairs must score identically (separate tenants avoid dedup)
    fair_rows = []
    jd_by_arch = {m["archetype"]: (jid, m) for jid, m in manifest["jds"].items()}
    cache: dict[tuple[str, str], tuple[float, float]] = {}

    def engine_score(fname: str, arch: str) -> tuple[float, float]:
        if (fname, arch) in cache:
            return cache[(fname, arch)]
        jid, meta = jd_by_arch[arch]
        t = "fair-" + re.sub(r"[^A-Za-z0-9_-]", "-", fname)[:50]
        data = (DATA / "fairness" / fname).read_bytes()
        ingest_bytes(t, fname, data)
        title, _, body = (DATA / "jds" / meta["file"]).read_text(encoding="utf-8").partition("\n")
        with session_scope() as s:
            job, _ = create_job(s, gw, t, title.strip(), body.strip())
            confirm_job(s, job)
            atoms = [a.text for a in job.atoms]
            jid_db = job.id
        rid = run_match_sync(t, jid_db)
        with session_scope() as s:
            r = s.scalars(select(MatchResult).where(MatchResult.run_id == rid)).first()
        kw = kw_score(data.decode("utf-8"), title.strip(), atoms)
        cache[(fname, arch)] = (r.score if r else 0.0, kw)
        return cache[(fname, arch)]

    for r in csv.DictReader(open(DATA / "gold" / "fairness_pairs.csv", encoding="utf-8")):
        base_e, base_k = engine_score(r["base_file"], r["archetype"])
        var_e, var_k = engine_score(r["variant_file"], r["archetype"])
        fair_rows.append({"base": r["base_file"], "variant": r["variant_file"], "attribute": r["varied_attribute"],
                          "engine_delta": round(var_e - base_e, 2), "keyword_delta": round(var_k - base_k, 2)})

    with session_scope() as s:
        runs = s.scalars(select(MatchRun).where(MatchRun.tenant_id == tenant)).all()
        flags = {cid_to_file[c.id]: c.flags for c in s.scalars(select(Candidate).where(Candidate.tenant_id == tenant))}

    summary = {}
    for name in ("engine", "keyword_ats", "bm25"):
        vals = [r["systems"][name] for r in per_jd]
        summary[name] = {m: round(sum(v[m] for v in vals) / len(vals), 3) for m in ("ndcg@10", "p@5", "recall@20")}
        summary[name]["stuffers_top10_total"] = sum(v["stuffers_top10"] for v in vals)
        tr = [v["transferable_rank"] for v in vals if v["transferable_rank"]]
        summary[name]["transferable_mean_rank"] = round(sum(tr) / len(tr), 1) if tr else None
    by_attr: dict[str, list[float]] = {}
    for f in fair_rows:
        by_attr.setdefault(f["attribute"], []).append(abs(f["engine_delta"]))
    result = {"mode": mode, "cvs": len(files), "jds": len(per_jd), "ingest_seconds": round(ingest_s, 2),
              "summary": summary, "per_jd": per_jd, "fairness": fair_rows,
              "fairness_max_abs_delta": {k: max(v) for k, v in by_attr.items()},
              "injection_flags": {f: [x for x in fl if "inject" in x or "hidden" in x] for f, fl in flags.items()
                                  if any("inject" in x or "hidden" in x for x in fl)},
              "total_cost_usd": round(sum(r.cost_usd for r in runs), 6)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "initial-synthetic-eval.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (OUT / "initial-synthetic-eval.md").write_text(render(result), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("fairness max |delta| by attribute:", result["fairness_max_abs_delta"])
    print(f"report: {OUT / 'initial-synthetic-eval.md'}")
    return 0


def render(r: dict) -> str:
    s = r["summary"]
    lines = [
        "# Initial evaluation on the synthetic dataset",
        "",
        f"Mode: **{r['mode']}** · {r['cvs']} CVs · {r['jds']} job descriptions · total model cost "
        f"**${r['total_cost_usd']:.4f}** · ingestion {r['ingest_seconds']}s. "
        "Generated by `scripts/evaluate.py`; rerun it after any change to extraction, retrieval or scoring.",
        "",
        "> **Read this first.** The gold labels come from the synthetic generator's ground truth, not from "
        "recruiters. The generator and the engine share a view of what \"evidence\" means, so a good result here is "
        "necessary, not sufficient. It shows the pipeline works and the core claim holds on controlled data. "
        "Real-world quality must be measured on a recruiter-graded, consented design-partner set (Phase 2 exit gate).",
        "",
        "## Summary (mean over job descriptions)",
        "",
        "| System | nDCG@10 | P@5 | Recall@20 | Keyword-stuffers in top 10 (all JDs) | Mean rank of the 'transferable' candidate |",
        "|---|---|---|---|---|---|",
    ]
    names = {"engine": "**Engine (evidence-based)**", "keyword_ats": "Keyword ATS baseline", "bm25": "BM25 baseline"}
    for k in ("engine", "keyword_ats", "bm25"):
        v = s[k]
        lines.append(f"| {names[k]} | {v['ndcg@10']:.3f} | {v['p@5']:.3f} | {v['recall@20']:.3f} | "
                     f"{v['stuffers_top10_total']} | {v['transferable_mean_rank']} |")
    lines += ["", "## Per job description", "",
              "| JD | Relevant (grade ≥2) | Engine nDCG@10 | Keyword nDCG@10 | BM25 nDCG@10 | Engine P@5 | "
              "Keyword P@5 | Transferable rank (engine / keyword) | Own keyword-stuffer rank (engine / keyword) | Run time |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for j in r["per_jd"]:
        e, k, b = j["systems"]["engine"], j["systems"]["keyword_ats"], j["systems"]["bm25"]
        lines.append(f"| {j['title']} | {j['relevant']} | {e['ndcg@10']:.3f} | {k['ndcg@10']:.3f} | {b['ndcg@10']:.3f} | "
                     f"{e['p@5']:.2f} | {k['p@5']:.2f} | {e['transferable_rank']} / {k['transferable_rank']} | "
                     f"{e['own_stuffer_rank']} / {k['own_stuffer_rank']} | {j['run_seconds']}s |")
    lines += ["", "## Fairness: counterfactual invariance", "",
              "Each pair has identical work evidence; only the varied attribute changes. The engine's score should "
              "not move (|Δ| = 0). Personal details are redacted before evaluation; names go to the PII vault.", "",
              "| Varied attribute | Max engine |Δ score| |", "|---|---|"]
    for a, d in r["fairness_max_abs_delta"].items():
        lines.append(f"| {a} | {d} |")
    lines += ["", "<details><summary>All fairness pairs</summary>", "",
              "| Base | Variant | Engine Δ | Keyword-ATS Δ |", "|---|---|---|---|"]
    for f in r["fairness"]:
        lines.append(f"| {f['base']} | {f['variant']} | {f['engine_delta']} | {f['keyword_delta']} |")
    lines += ["", "</details>", "", "## Prompt-injection and hidden-text handling", ""]
    for f, fl in r["injection_flags"].items():
        lines.append(f"- `{f}`: {', '.join(fl)} (text removed before evaluation, recruiter sees a flag)")
    lines += ["", "## What this does not show", "",
              "- Real CV variety (scans, multi-column PDFs, regional formats, non-English text).",
              "- Recruiter agreement: grades here are rule-based, not human judgements.",
              "- LLM-mode quality and cost: run `scripts/evaluate.py --use-env` with an OpenRouter key to measure "
              "them (the gateway enforces the $0.20/run cap).",
              "- Scale: 64 CVs. Latency at 100K+ candidates needs the pgvector build (ADR 0009).", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
