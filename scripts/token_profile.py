"""Token profile of every LLM task, measured on the exact production prompts, at $0.

    backend/.venv/Scripts/python.exe -X utf8 scripts/token_profile.py --label baseline
    backend/.venv/Scripts/python.exe -X utf8 scripts/token_profile.py --label optimized

How it works
- Runs the real pipeline (65 synthetic CVs, 7 JDs, 7 match runs, 3 searches) in a throw-away
  data folder. A recording gateway stands in for OpenRouter: every chat_json call is captured
  exactly as it would be sent (task, model, system, user, max_tokens, reasoning setting).
  Nothing leaves the machine and nothing is spent.
- The recording gateway answers with schema-valid stand-in outputs built from the prompt itself
  (taxonomy matches per bullet, quoted exactly), so later stages see realistic LLM claims.
  Every 10th CV gets quotes that fail validation in half of its units, which runs the real
  escalation path (a 10% escalation rate).
- Output tokens are those stand-ins: an estimate of what a model returns for the same prompt,
  not a measurement. Hidden thinking is reported separately, as a worst case.
- Tokens are estimated at 3 characters per token (pricing.estimate_tokens). The cost meter
  records the provider's real counts on the first OpenRouter run.
Writes docs/eval/token-profile.json (one entry per --label) and docs/eval/token-profile.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "synthetic"
OUT = ROOT / "docs" / "eval"
SEARCHES = ["python developer who has shipped RAG to production in Bengaluru",
            "data engineer with Spark and Airflow, 3+ years",
            "frontend engineer React TypeScript, remote"]
CACHE_MIN = 512          # minimum cacheable prefix, Claude Haiku 5.5 / Sonnet 5.5 (prompt-caching docs)
THINKING_FLOOR = 1024    # OpenRouter's minimum Anthropic reasoning budget

UNIT_RE = re.compile(r'<unit id="([^"]+)"([^>]*)>\n(.*?)\n</unit>', re.S)
KIND_RE = re.compile(r'kind="([^"]*)"')


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _units(user: str) -> list[tuple[str, str, str]]:
    out = []
    for uid, attrs, body in UNIT_RE.findall(user):
        m = KIND_RE.search(attrs)
        out.append((uid, m.group(1) if m else "experience", body))
    return out


def _quote_max(system: str) -> int:
    m = re.search(r"max (\d+) char", system)
    return int(m.group(1)) if m else 200


def _array_key_and_props(schema: dict) -> tuple[str, dict]:
    for k, v in schema["properties"].items():
        if v.get("type") == "array" and isinstance(v.get("items"), dict) and v["items"].get("type") == "object":
            return k, v["items"]["properties"]
    raise ValueError("no object array in schema")


def stand_in_extract(system: str, user: str, schema: dict, tax, corrupt: bool) -> dict:
    """What a careful model would return: one row per (bullet, skills) with an exact quote."""
    key, props = _array_key_and_props(schema)
    grouped = "skills" in props
    qmax = _quote_max(system)
    units = _units(user)
    rows: list[dict] = []
    bad_units = {uid for i, (uid, _, _) in enumerate(units) if i % 2 == 0} if corrupt else set()
    for uid, kind, body in units:
        for i, ln in enumerate(_lines(body)):
            if kind in ("experience", "project"):
                if i == 0 or not (ln.startswith(("-", "•", "*")) or ln.lower().startswith("tech stack")):
                    continue  # header lines are context, never evidence
                level = "applied" if ln.lower().startswith("tech stack") else "demonstrated"
                spans = [ln.lstrip("-•* ").strip()]
            else:
                level = "listed_only"
                spans = [ln]  # a quote must be >= 12 chars, so a model quotes the whole list line
            for span in spans:
                sids = tax.skill_ids_in(span)
                if not sids:
                    continue
                q = span[:qmax]
                if uid in bad_units:
                    q = "a sentence that is not in this CV"
                if grouped:
                    rows.append({"unit": uid, "quote": q, "level": level, "skills": sids})
                else:
                    rows.extend({"unit": uid, "skill": s, "level": level, "quote": q} for s in sids)
    return {key: rows, "unmapped_terms": []}


def stand_in_verify(system: str, user: str) -> dict:
    m = re.search(r"Judge only these requirement ids: ([\d, ]+)", user)
    ids = [x.strip() for x in m.group(1).split(",")] if m else re.findall(r"^(\d+): \[", system, re.M)
    qmax = _quote_max(system)
    bullets = [ln.lstrip("-•* ") for _, _, b in _units(user) for ln in _lines(b)[1:]] or ["x" * 120]
    return {"verdicts": [{"atom": a, "level": "applied", "unit": "u1", "quote": bullets[i % len(bullets)][:qmax]}
                         for i, a in enumerate(ids)]}


def stand_in_deep(system: str, user: str, schema: dict) -> dict:
    m = re.search(r"summary <= (\d+) words\. Up to (\d+) strengths, (\d+) gaps, (\d+) interview questions", system)
    words, ns, ng, nq = (int(x) for x in m.groups()) if m else (70, 4, 5, 4)
    quotes = re.findall(r'— "([^"]+)"', user) or ["x" * 120]
    return {"summary": " ".join(["evidence"] * words),
            "strengths": [{"text": "Built and shipped the described capability in production", "ref": f"E{i + 1}"}
                          if "ref" in schema["properties"]["strengths"]["items"]["properties"] else
                          {"text": "Built and shipped the described capability in production", "unit": "u1",
                           "quote": quotes[i % len(quotes)]} for i in range(ns)],
            "gaps": ["No described work with the required capability; only listed in the skills section"] * ng,
            "interview_questions": ["Walk me through a system you built for this: your own role, the design "
                                    "choices you made, and how you measured the outcome?"] * nq}


def stand_in_jd(user: str, tax) -> dict:
    from nsm.jobs.requirements import parse_jd_heuristic
    title, _, rest = user.partition("\n\nJob description:\n")
    d = parse_jd_heuristic(title.replace("Job title (context only): ", ""), rest, tax)
    return {"seniority_years": d.seniority_years, "location": d.location, "work_mode": d.work_mode,
            "atoms": [{"text": a.text, "kind": a.kind, "dimension": a.dimension, "skills": a.skill_ids,
                       "logic": a.logic, "min_years": a.min_years} for a in d.atoms]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    os.environ["NSM_DATA_DIR"] = tempfile.mkdtemp(prefix="nsm-tokens-")
    os.environ.update(NSM_LLM_MODE="offline", NSM_EMBEDDING_MODE="local", NSM_RERANK_MODE="none")

    import nsm.gateway as gwmod
    from nsm.config import get_settings
    from nsm.db import init_db, session_scope
    from nsm.gateway import MAX_TOKENS, Gateway, scrub
    from nsm.gateway.pricing import cost_usd, estimate_tokens, price
    from nsm.ingestion.pipeline import ingest_bytes
    from nsm.jobs.requirements import parse_jd_llm
    from nsm.jobs.service import confirm_job, create_job
    from nsm.matching.service import run_match_sync
    from nsm.taxonomy import get_taxonomy

    tax = get_taxonomy()
    thinking_off = getattr(gwmod, "THINKING_DEFAULT_OFF", False)
    effort_map = getattr(gwmod, "EFFORT", {})

    class RecordingGateway(Gateway):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[dict] = []
            self.n_extract = 0

        @property
        def llm_enabled(self) -> bool:  # type: ignore[override]
            return True

        def chat_json(self, session, *, tenant_id, task, system, user, schema, schema_name, run_id=None,
                      candidate_id=None, bulk=False):
            user = scrub(user)
            if task == "extract":
                self.n_extract += 1
                data = stand_in_extract(system, user, schema, tax, corrupt=self.n_extract % 10 == 1)
                shown = stand_in_extract(system, user, schema, tax, corrupt=False)
            elif task == "escalate":
                data = shown = stand_in_extract(system, user, schema, tax, corrupt=False)
            elif task == "verify":
                shown, data = stand_in_verify(system, user), {"verdicts": []}  # keep offline verdicts
            elif task == "deep":
                shown, data = stand_in_deep(system, user, schema), {"summary": "", "strengths": [], "gaps": [],
                                                            "interview_questions": []}
            else:  # jd / search
                shown = stand_in_jd(user, tax)
                data = {"seniority_years": None, "location": None, "work_mode": None, "atoms": []}
            self.calls.append({"task": task, "model": self.model_for(task, bulk=bulk), "run_id": run_id,
                               "candidate_id": candidate_id, "system": system, "user": user,
                               "max_tokens": MAX_TOKENS.get(task, 1000), "effort": effort_map.get(task),
                               "out": json.dumps(shown, ensure_ascii=False)})
            return data

    gw = RecordingGateway()
    gwmod._gateway = gw
    init_db()
    tenant = "tokens"
    files = sorted(f for f in (DATA / "cvs").iterdir() if "_resent" not in f.name)
    for f in files:
        ingest_bytes(tenant, f.name, f.read_bytes(), source="synthetic")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for jd_meta in manifest["jds"].values():
        title, _, body = (DATA / "jds" / jd_meta["file"]).read_text(encoding="utf-8").partition("\n")
        with session_scope() as s:
            job, _ = create_job(s, gw, tenant, title.strip(), body.strip(), use_llm=False)
            confirm_job(s, job)  # the heuristic draft: identical atoms in every profile run
            job_id = job.id
        with session_scope() as s:
            parse_jd_llm(s, gw, tenant, title.strip(), body.strip())  # profile the JD call on its own
        run_match_sync(tenant, job_id)
    with session_scope() as s:
        for q in SEARCHES:
            parse_jd_llm(s, gw, tenant, "Search", q, task="search")

    fee = 1 + get_settings().openrouter_fee

    def thinking_tokens(c: dict, visible: int) -> int:
        room = max(0, c["max_tokens"] - visible)
        if thinking_off and c["effort"] is None:
            return 0
        if c["effort"] is None:
            return room  # model default (Haiku 5.5 medium, Sonnet 5.5 high): bounded only by max_tokens
        ratio = {"minimal": 0.1, "low": 0.2, "medium": 0.5, "high": 0.8}.get(c["effort"], 0.8)
        return min(room, max(int(c["max_tokens"] * ratio), THINKING_FLOOR))

    rows = []
    for c in gw.calls:
        sys_t, usr_t, out_t = estimate_tokens(c["system"]), estimate_tokens(c["user"]), estimate_tokens(c["out"])
        think = thinking_tokens(c, out_t)
        pi, po, pc = price(c["model"])
        visible = cost_usd(c["model"], sys_t + usr_t, out_t) * fee
        warm = ((sys_t * pc if sys_t >= CACHE_MIN else sys_t * pi) + usr_t * pi + out_t * po) / 1e6 * fee
        rows.append({**{k: c[k] for k in ("task", "model", "run_id", "candidate_id", "max_tokens", "effort")},
                     "system_tokens": sys_t, "user_tokens": usr_t, "output_tokens": out_t, "thinking_max": think,
                     "usd": visible, "usd_warm_cache": warm, "usd_thinking_max": think * po / 1e6 * fee,
                     # thinking off, but the provider refuses it for this model: lowest effort, reserve allowed
                     "usd_thinking_fallback": (THINKING_FLOOR * po / 1e6 * fee
                                               if thinking_off and "sonnet" in c["model"] else 0.0)})

    def agg(task: str) -> dict:
        rs = [r for r in rows if r["task"] == task]
        if not rs:
            return {"calls": 0}
        n = len(rs)
        avg = lambda k: sum(r[k] for r in rs) / n  # noqa: E731
        return {"calls": n, "model": rs[0]["model"], "system_tokens": round(avg("system_tokens")),
                "user_tokens": round(avg("user_tokens")), "output_tokens": round(avg("output_tokens")),
                "output_tokens_max": max(r["output_tokens"] for r in rs),
                "max_tokens": rs[0]["max_tokens"], "effort": rs[0]["effort"],
                "system_cacheable": avg("system_tokens") >= CACHE_MIN, "thinking_max": round(avg("thinking_max")),
                "usd": avg("usd"), "usd_warm_cache": avg("usd_warm_cache"), "usd_thinking_max": avg("usd_thinking_max")}

    tasks = {t: agg(t) for t in ("extract", "escalate", "jd", "verify", "deep", "search")}
    n_cv = len(files)
    by_run = defaultdict(lambda: {"verify": 0, "deep": 0, "usd": 0.0, "usd_warm": 0.0, "think": 0.0, "fb": 0.0})
    for r in rows:
        if r["run_id"]:
            b = by_run[r["run_id"]]
            b[r["task"]] += 1
            b["usd"] += r["usd"]
            b["usd_warm"] += r["usd_warm_cache"]
            b["think"] += r["usd_thinking_max"]
            b["fb"] += r["usd_thinking_fallback"]
    cv_rows = [r for r in rows if r["task"] in ("extract", "escalate")]
    runs = list(by_run.values())
    result = {
        "label": args.label, "cvs": n_cv, "runs": len(runs),
        "tasks": tasks,
        "per_cv": {"usd": sum(r["usd"] for r in cv_rows) / n_cv,
                   "usd_warm_cache": sum(r["usd_warm_cache"] for r in cv_rows) / n_cv,
                   "usd_thinking_max": sum(r["usd_thinking_max"] for r in cv_rows) / n_cv,
                   "usd_thinking_fallback": sum(r["usd_thinking_fallback"] for r in cv_rows) / n_cv,
                   "escalation_rate": tasks["escalate"]["calls"] / n_cv},
        "per_run": {"verify_calls": sum(b["verify"] for b in runs) / len(runs),
                    "deep_calls": sum(b["deep"] for b in runs) / len(runs),
                    "usd": sum(b["usd"] for b in runs) / len(runs),
                    "usd_warm_cache": sum(b["usd_warm"] for b in runs) / len(runs),
                    "usd_thinking_max": sum(b["think"] for b in runs) / len(runs),
                    "usd_thinking_fallback": sum(b["fb"] for b in runs) / len(runs)},
        "per_jd": {"usd": tasks["jd"]["usd"], "usd_thinking_max": tasks["jd"]["usd_thinking_max"]},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    jpath = OUT / "token-profile.json"
    allres = json.loads(jpath.read_text(encoding="utf-8")) if jpath.exists() else {}
    allres[args.label] = result
    jpath.write_text(json.dumps(allres, indent=2), encoding="utf-8")
    (OUT / "token-profile.md").write_text(render(allres), encoding="utf-8")
    print(render({args.label: result}))
    return 0


def _usd(x: float) -> str:
    return f"${x:.5f}" if x < 0.01 else f"${x:.4f}"


def render(allres: dict) -> str:
    out = ["# Token profile per LLM task", "",
           "Generated by `scripts/token_profile.py`. It measures the exact prompts the pipeline sends, on the "
           "synthetic dataset, at $0. Input tokens are measured. Output tokens are stand-in answers of the "
           "requested shape. Tokens are estimated at 3 characters per token. Costs include the 5.5% "
           "OpenRouter fee. *Warm cache* reads a system prompt of 512 tokens or more from the cache at 0.1×. "
           "*Thinking, worst case* is the hidden reasoning the request allows, billed as output.", ""]
    for label, r in allres.items():
        out += [f"## {label}", "",
                "| Task | Model | Calls | System in | User in | Output | max_tokens | Thinking allowed | $ / call | "
                "$ / call, warm cache | + thinking, worst case |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for t, a in r["tasks"].items():
            if not a.get("calls"):
                continue
            out.append(f"| {t} | {a['model'].split('/')[-1]} | {a['calls']} | {a['system_tokens']:,}"
                       f"{' (cacheable)' if a['system_cacheable'] else ''} | {a['user_tokens']:,} | "
                       f"{a['output_tokens']:,} | {a['max_tokens']:,} | {a['thinking_max']:,} | {_usd(a['usd'])} | "
                       f"{_usd(a['usd_warm_cache'])} | {_usd(a['usd_thinking_max'])} |")
        cv, run = r["per_cv"], r["per_run"]

        def fb(x: dict) -> str:
            v = x.get("usd_thinking_fallback", 0.0)
            return f" If OpenRouter refuses thinking-off for Sonnet 5.5: up to {_usd(v)} more." if v else ""
        out += ["", f"- **Per CV** (extract + escalation at {cv['escalation_rate']:.0%}): {_usd(cv['usd'])}; "
                    f"{_usd(cv['usd_warm_cache'])} with a warm cache; up to {_usd(cv['usd_thinking_max'])} more "
                    "in hidden thinking." + fb(cv),
                f"- **Per match run** ({run['verify_calls']:.0f} verify + {run['deep_calls']:.0f} deep): "
                f"{_usd(run['usd'])}; {_usd(run['usd_warm_cache'])} with a warm cache; up to "
                f"{_usd(run['usd_thinking_max'])} more in hidden thinking." + fb(run), ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
