"""Token-cost model for the Neural-Semantic Matching platform.

All prices are USD per 1M tokens: (input, output, cache_read, source).
Run:  python -I cost_model.py            -> prints markdown tables
      python -I cost_model.py TEMPLATE OUT -> fills {{PLACEHOLDERS}} in TEMPLATE, writes OUT
"""
import sys
from copy import deepcopy

PRICES = {
    "haiku-5.5":      (0.10, 0.50, 0.01,  "Anthropic list; cache read assumed 10%"),
    "sonnet-5.5":     (2.00, 10.00, 0.20, "Anthropic list"),
    "opus-5.5":       (4.00, 20.00, 0.20, "Anthropic list"),
    "gpt-5-nano":     (0.05, 0.40, 0.005, "aggregator (indicative); cache read assumed 10%"),
    "qwen-flash":     (0.03, 0.13, 0.003, "aggregator (indicative); cache read assumed 10%"),
    "deepseek-flash": (0.045, 0.50, 0.0045, "aggregator (indicative); cache read assumed 10%"),
}
OPENROUTER_FEE = 0.055

BASE = dict(
    # ingestion
    cv_tokens=2000, extract_prompt=1500, extract_out=1500, extract_prompt_cached=False,
    escalation=0.10, embed_tokens=3000, embed_price=0.0,  # 0 = self-hosted
    # per job
    jd_in=4000, jd_out=2000,
    rubric=1500, evidence_pack=2500, verify_out=600, deep_out=1000,
    verify_k=50, deep_k=10, report_views=10, report_in=1200, report_out=300,
    rerank_cost=0.0,
    # NL search
    nl_in=800, nl_out=400, nl_verify_k=20,
    # behaviour
    reasoning=0.2,          # reasoning/thinking tokens as a multiple of visible output
    small="haiku-5.5", verify="haiku-5.5", mid="sonnet-5.5", deep="sonnet-5.5",
    fee=OPENROUTER_FEE,
)

PROFILES = {
    "Lean (open small models)": dict(small="qwen-flash", verify="qwen-flash", escalation=0.05, deep_k=5),
    "Baseline (Phase 1 estimate)": dict(),
    "Target (self-hosted embed/rerank)": dict(escalation=0.05, deep_k=5),
    # Decision D-016: no GPU, so the MVP uses API embeddings (indicative $0.10/M) and API rerank (~$0.006/run)
    "MVP (Target + API embed/rerank)": dict(escalation=0.05, deep_k=5, embed_price=0.10, rerank_cost=0.006),
    "Quality-max": dict(verify="sonnet-5.5", deep="opus-5.5", deep_k=20, escalation=0.20, reasoning=1.0),
}


def call(model, fresh_in, cached_in, out_visible, reasoning):
    pi, po, pc, _ = PRICES[model]
    return (fresh_in * pi + cached_in * pc + out_visible * (1 + reasoning) * po) / 1e6


def per_cv(p):
    fresh = p["cv_tokens"] + (0 if p["extract_prompt_cached"] else p["extract_prompt"])
    cached = p["extract_prompt"] if p["extract_prompt_cached"] else 0
    small = call(p["small"], fresh, cached, p["extract_out"], p["reasoning"])
    esc = p["escalation"] * call(p["mid"], fresh, cached, p["extract_out"], p["reasoning"])
    embed = p["embed_tokens"] * p["embed_price"] / 1e6
    return dict(extract=small, escalation=esc, embed=embed, total=(small + esc + embed) * (1 + p["fee"]))


def per_job(p):
    r = p["reasoning"]
    jd = call(p["mid"], p["jd_in"], 0, p["jd_out"], r)
    verify = p["verify_k"] * call(p["verify"], p["evidence_pack"], p["rubric"], p["verify_out"], r)
    deep = p["deep_k"] * call(p["deep"], p["evidence_pack"], p["rubric"], p["deep_out"], r)
    report = p["report_views"] * call(p["small"], p["report_in"], 0, p["report_out"], r)
    sub = jd + verify + deep + report + p["rerank_cost"]
    return dict(jd=jd, verify=verify, deep=deep, report=report, rerank=p["rerank_cost"], total=sub * (1 + p["fee"]))


def per_search(p):
    r = p["reasoning"]
    q = call(p["small"], p["nl_in"], 0, p["nl_out"], r)
    v = p["nl_verify_k"] * call(p["verify"], p["evidence_pack"], p["rubric"], p["verify_out"], r)
    return (q + v) * (1 + p["fee"])


def naive_per_candidate(p):
    # mid-tier model reads JD rubric (cached) + full CV, writes a verdict, for every candidate
    return call(p["mid"], p["cv_tokens"], p["rubric"], p["deep_out"], p["reasoning"]) * (1 + p["fee"])


def with_(**kw):
    p = deepcopy(BASE)
    p.update(kw)
    return p


def money(x):
    if x == 0:
        return "$0"
    if x < 0.01:
        return f"${x:.4f}"
    if x < 1:
        return f"${x:.3f}"
    if x < 100:
        return f"${x:,.2f}"
    return f"${x:,.0f}"


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def t_prices():
    def pr(x):
        s = f"{x:.4f}".rstrip("0")
        return "$" + (s if len(s.split(".")[1]) >= 2 else f"{x:.2f}")
    rows = [[m, pr(v[0]), pr(v[1]), pr(v[2]), v[3]] for m, v in PRICES.items()]
    return table(["Model", "Input /1M", "Output /1M", "Cache read /1M", "Source"], rows)


def t_extraction_models():
    rows = []
    for m in PRICES:
        c = per_cv(with_(small=m, escalation=0.0))
        rows.append([m, money(c["total"]), money(c["total"] * 1_000_000)])
    return table(["Extraction model (no escalation)", "Per CV", "Per 1M CVs"], rows)


def t_per_cv():
    c = per_cv(BASE)
    rows = [
        ["Small-model extraction", money(c["extract"])],
        [f"Escalation to mid tier ({int(BASE['escalation']*100)}% of CVs)", money(c["escalation"])],
        ["Embeddings (self-hosted)", money(c["embed"])],
        [f"**Total incl. {BASE['fee']*100:.1f}% OpenRouter fee**", f"**{money(c['total'])}**"],
    ]
    return table(["Step", "Cost per CV"], rows)


def t_per_job():
    j = per_job(BASE)
    rows = [
        ["JD → requirement atoms (mid)", money(j["jd"])],
        [f"Verify top {BASE['verify_k']} (small, rubric cached)", money(j["verify"])],
        [f"Deep eval top {BASE['deep_k']} (mid)", money(j["deep"])],
        [f"Report narratives ({BASE['report_views']} viewed)", money(j["report"])],
        ["Rerank (self-hosted)", money(j["rerank"])],
        [f"**Total incl. fee**", f"**{money(j['total'])}**"],
    ]
    return table(["Step", "Cost per match run"], rows)


def t_profiles():
    rows = []
    for name, kw in PROFILES.items():
        p = with_(**kw)
        rows.append([name, f"{p['small']} / {p['verify']} / {p['deep']}", money(per_cv(p)["total"]),
                     money(per_job(p)["total"]), money(per_search(p))])
    return table(["Profile", "Extract / verify / deep-eval models", "Per CV", "Per match run", "Per NL search"], rows)


def t_sensitivity():
    base_cv, base_job = per_cv(BASE)["total"], per_job(BASE)["total"]
    cases = [
        ("Reasoning tokens = 0× visible output", dict(reasoning=0.0)),
        ("Reasoning tokens = 1× visible output", dict(reasoning=1.0)),
        ("Reasoning tokens = 3× visible output", dict(reasoning=3.0)),
        ("CV length 1K tokens", dict(cv_tokens=1000)),
        ("CV length 4K tokens", dict(cv_tokens=4000)),
        ("Escalation 0%", dict(escalation=0.0)),
        ("Escalation 30%", dict(escalation=0.30)),
        ("Verify top 20", dict(verify_k=20)),
        ("Verify top 100", dict(verify_k=100)),
        ("Deep eval top 5", dict(deep_k=5)),
        ("Deep eval top 20", dict(deep_k=20)),
        ("Deep eval on Haiku 5.5", dict(deep="haiku-5.5")),
        ("Deep eval on Opus 5.5", dict(deep="opus-5.5")),
        ("Embeddings via API ($0.10/M)", dict(embed_price=0.10)),
        ("Embeddings via API ($0.02/M)", dict(embed_price=0.02)),
        ("Rerank via API (~$0.006/run)", dict(rerank_cost=0.006)),
        ("Extraction prompt cached", dict(extract_prompt_cached=True)),
        ("No OpenRouter fee (direct provider)", dict(fee=0.0)),
    ]
    rows = [["**Baseline**", f"**{money(base_cv)}**", "—", f"**{money(base_job)}**", "—"]]
    for label, kw in cases:
        p = with_(**kw)
        cv, job = per_cv(p)["total"], per_job(p)["total"]
        rows.append([label, money(cv), f"{(cv/base_cv-1)*100:+.0f}%", money(job), f"{(job/base_job-1)*100:+.0f}%"])
    return table(["Change (one at a time)", "Per CV", "Δ", "Per match run", "Δ"], rows)


def t_scale():
    rows = []
    cv, job, naive = per_cv(BASE)["total"], per_job(BASE)["total"], naive_per_candidate(BASE)
    for n in (1_000, 10_000, 100_000, 1_000_000):
        rows.append([f"{n:,}", money(n * cv), money(0.05 * n * cv), money(job), money(n * naive),
                     f"{n * naive / job:,.0f}×"])
    return table(["Candidates", "One-time ingestion", "Monthly refresh (5%)", "Per match run (funnel)",
                  "Per match run (naive: mid-tier on every CV)", "Naive ÷ funnel"], rows)


SCENARIOS = [
    ("Small agency", 10_000, 1_000, 20 * 2, 200),
    ("Mid-size staffing firm", 100_000, 5_000, 200 * 2, 2_000),
    ("Large IT services firm", 1_000_000, 30_000, 1_000 * 2, 10_000),
]


def t_monthly():
    rows = []
    for name, stock, new, runs, searches in SCENARIOS:
        for pname in ("Lean (open small models)", "MVP (Target + API embed/rerank)", "Baseline (Phase 1 estimate)"):
            p = with_(**PROFILES[pname])
            cv, job, s = per_cv(p)["total"], per_job(p)["total"], per_search(p)
            ingest = (new + 0.05 * stock) * cv
            match = runs * job
            search = searches * s
            rows.append([name, pname.split(" (")[0], f"{stock:,} / +{new:,}", runs, f"{searches:,}",
                         money(ingest), money(match), money(search), f"**{money(ingest + match + search)}**"])
    return table(["Customer", "Profile", "Stock / new per month", "Match runs", "NL searches",
                  "Ingest + refresh", "Matching", "Search", "Monthly tokens"], rows)


def t_assumptions():
    b = BASE
    rows = [
        ["CV text", f"{b['cv_tokens']:,} tokens"],
        ["Extraction instructions + schema", f"{b['extract_prompt']:,} tokens (not cached by default — may be under the model's minimum cacheable prefix)"],
        ["Extraction output (structured JSON)", f"{b['extract_out']:,} tokens"],
        ["Reasoning/thinking tokens", f"{b['reasoning']}× visible output (low effort); billed as output"],
        ["Escalation to mid tier", f"{int(b['escalation']*100)}% of CVs"],
        ["Embedded tokens per CV", f"{b['embed_tokens']:,} (self-hosted ⇒ $0 marginal)"],
        ["JD intake", f"{b['jd_in']:,} in / {b['jd_out']:,} out (mid tier)"],
        ["Rubric (cached across candidates)", f"{b['rubric']:,} tokens"],
        ["Evidence pack per candidate", f"{b['evidence_pack']:,} tokens"],
        ["Verify / deep-eval output", f"{b['verify_out']} / {b['deep_out']} tokens"],
        ["Shortlist sizes", f"verify top {b['verify_k']}, deep eval top {b['deep_k']}"],
        ["Report narratives", f"{b['report_views']} per run, {b['report_in']} in / {b['report_out']} out"],
        ["NL search", f"{b['nl_in']} in / {b['nl_out']} out parse + verify top {b['nl_verify_k']}"],
        ["Gateway fee", f"{b['fee']*100:.1f}% (OpenRouter credit purchase)"],
    ]
    return table(["Input", "Baseline value"], rows)


TABLES = {
    "PRICES": t_prices, "ASSUMPTIONS": t_assumptions, "EXTRACTION_MODELS": t_extraction_models,
    "PER_CV": t_per_cv, "PER_JOB": t_per_job, "PROFILES": t_profiles, "SENSITIVITY": t_sensitivity,
    "SCALE": t_scale, "MONTHLY": t_monthly,
}

if __name__ == "__main__":
    if len(sys.argv) == 3:
        src = open(sys.argv[1], encoding="utf-8").read()
        for k, fn in TABLES.items():
            src = src.replace("{{" + k + "}}", fn())
        assert "{{" not in src, "unfilled placeholder"
        open(sys.argv[2], "w", encoding="utf-8").write(src)
        print("wrote", sys.argv[2])
    else:
        for k, fn in TABLES.items():
            print(f"\n## {k}\n{fn()}")
