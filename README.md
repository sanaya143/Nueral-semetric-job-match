# Neural-Semantic Matching: initial local build

Ranks candidates by **evidence of work**, not keyword density. Each CV is split into evidence units (roles and projects). Each job becomes requirements you confirm (must / nice-to-have / learnable). Every verdict quotes the CV, and a deterministic scorer computes the ranking. The recruiter makes the decision.

This is the **initial build**: local, single user, no Docker. It uses **SQLite + local files** and runs **offline at $0** by default, with an optional **OpenRouter mode**. Production readiness (Postgres/pgvector, auth, queue, OCR) is a later phase; see [ADR 0009](docs/adr/0009-initial-local-build.md).

## What's in it

| Area | In this build |
|---|---|
| Ingestion | PDF / DOCX / TXT / MD / ZIP upload; hidden-text stripping; prompt-injection flags; PII moved to a separate contact vault; exact-duplicate detection |
| Understanding | Evidence units + capability claims graded **demonstrated / applied / inferred / listed only**; skills taxonomy with is-a and implies relations; job titles are never evidence |
| Jobs | JD → requirement atoms → **you edit and confirm** → versioned; edits after confirmation create a new draft version |
| Matching | LangGraph run: hard filters → hybrid retrieval per requirement (dense + BM25 + claims, RRF) → rerank → LLM verification (OpenRouter mode) → deterministic score → deep evaluation |
| Reports | Per-requirement verdicts with cited quotes, gaps, things to verify, interview questions, score breakdown, provenance; side-by-side compare of 2–3 candidates; shortlist / maybe / reject feedback |
| Search | Natural-language search over the same funnel |
| Trust | Blind review (default on), verified hard erase, audit log, per-call cost meter and budget caps, counterfactual fairness tests |
| Data | Synthetic dataset (65 CVs, 7 JDs, gold labels, fairness pairs) + evaluation script vs keyword-ATS and BM25 baselines |

## Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm

## Quick start (offline mode, $0, no key)

Run each command from the project root (`D:\Learning\Project1`).

1. Install the backend:

```bash
cd backend && uv sync --python 3.13
```

2. Install and build the dashboard:

```bash
npm --prefix frontend install
```

```bash
npm --prefix frontend run build
```

3. Load the synthetic demo data. This ingests 65 CVs, creates and confirms 7 jobs, and runs 7 matches (about 5 seconds):

```bash
backend/.venv/Scripts/python.exe -X utf8 scripts/seed_demo.py --reset
```

4. Start the app, then open http://127.0.0.1:8000:

```bash
backend/.venv/Scripts/python.exe -X utf8 -m uvicorn nsm.api.app:app --app-dir backend --host 127.0.0.1 --port 8000
```

For frontend development with hot reload, keep the API running and start Vite. Then open http://localhost:5173; it proxies `/api` to port 8000:

```bash
npm --prefix frontend run dev
```

## OpenRouter mode (real LLM calls)

Offline mode uses deterministic extraction, rule-based verdicts and local hash embeddings. OpenRouter mode adds LLM extraction, LLM verification of the top 50, and LLM deep evaluation of the top 5.

1. Copy `.env.example` to `.env` in the project root. `.env` is git-ignored.
2. Create a key at https://openrouter.ai/keys. Set a credit limit on it as a second safety net.
3. In `.env`, paste **your** key after `NSM_OPENROUTER_API_KEY=` and set `NSM_LLM_MODE=openrouter`.
4. Restart the API. The sidebar badge changes from *Offline ($0)* to *OpenRouter*.

Every request is sent with `data_collection: deny`, zero-data-retention routing, and PII (emails, phone numbers, URLs) scrubbed. Names are already in the vault.

### What each action costs

Prices are OpenRouter list prices (verified 2026-11-05), plus the 5.5% credit fee. Haiku 5.5 costs $0.10 / $0.50 and Sonnet 5.5 costs $2 / $10 per 1M input / output tokens.

| Action | Model | Typical cost | Hard stop |
|---|---|---|---|
| Ingest one CV | Haiku 5.5, ~1.6K in / ~0.4K out (Sonnet 5.5 re-labels only the failed sections if >30% of quotes fail validation) | ≈ $0.0004; escalation adds ≈ $0.0067; average ≈ $0.0011 per CV at an 11% escalation rate ($0.0007 with a warm prompt cache) | $0.024 for any single CV (D-023) |
| Parse one JD | Sonnet 5.5, ~0.9K in / ~0.5K out | ≈ $0.007 | monthly cap |
| Match run with LLM verification | Haiku verifies 50 (≈ $0.00026 each); Sonnet deep-evaluates 5 (≈ $0.009 each) | ≈ $0.06 | $0.20 per run; remaining candidates keep offline verdicts |
| Extra deep evaluation (ranks 6–10, on click) | Sonnet 5.5, ~0.9K in / ≤1K out | ≈ $0.009 | per-run cap |
| Search with "interpret with LLM" | Haiku 5.5, ~0.7K in / ≤0.3K out | ≈ $0.0001 | monthly cap |

Hidden thinking is switched off on every call (D-024). Claude Haiku 5.5 and Sonnet 5.5 think by default, and those tokens are billed as output. If OpenRouter refuses thinking-off for Sonnet 5.5, those calls fall back to low effort, and a run can cost up to ≈ $0.11. The **Thinking** column on the Costs page shows which case you are in.

These figures come from `scripts/token_profile.py`, which replays the exact prompts at $0 and estimates output tokens ([docs/eval/token-profile.md](docs/eval/token-profile.md)). The cost meter records the real counts on your first run.
| API embeddings (`NSM_EMBEDDING_MODE=openrouter`) | baai/bge-m3, ~3K tokens per CV | ≈ $0.00003 per CV | monthly cap |

The monthly cap is $50 (D-019). Each call is checked against its worst-case cost *before* it is sent, and every call is metered on the **Costs & audit** page. Bulk ingestion can use `:batch` model variants at half price (`NSM_USE_BATCH_VARIANTS=true`). Full model: [docs/02-token-cost-model.md](docs/02-token-cost-model.md).

## Everyday commands

Run the backend tests (41):

```bash
cd backend && .venv/Scripts/python.exe -X utf8 -m pytest -q
```

Evaluate the engine against the keyword-ATS and BM25 baselines (writes `docs/eval/initial-synthetic-eval.md`):

```bash
backend/.venv/Scripts/python.exe -X utf8 scripts/evaluate.py
```

Measure tokens and cost per LLM task on the exact prompts, offline at $0 (writes `docs/eval/token-profile.md`):

```bash
backend/.venv/Scripts/python.exe -X utf8 scripts/token_profile.py --label current
```

Regenerate the synthetic dataset (deterministic, seed 20261106):

```bash
backend/.venv/Scripts/python.exe -X utf8 scripts/generate_synthetic.py
```

Latest evaluation (synthetic gold, offline mode):

| System | nDCG@10 | P@5 | Keyword-stuffers in top 10 | Transferable candidate's mean rank |
|---|---|---|---|---|
| **Engine** | **0.974** | **0.971** | **1** | **3.3** |
| Keyword ATS | 0.872 | 0.857 | 8 | 8.9 |
| BM25 | 0.840 | 0.914 | 8 | 9.0 |

Fairness: the score moved **0.0** for name/gender swaps, surname swaps, added personal details, and a 2-year career gap. Synthetic gold labels show that the pipeline works; real quality needs a recruiter-graded design-partner set (Phase 2 gate).

## Layout

```
backend/nsm/      FastAPI app, ingestion, extraction, taxonomy, gateway, retrieval, scoring, matching (LangGraph)
backend/tests/    pytest suite (offline; LLM paths tested with a fake client)
frontend/         React + TypeScript dashboard (Vite)
scripts/          generate_synthetic.py, seed_demo.py, evaluate.py, token_profile.py
datasets/         synthetic CVs, JDs, gold labels, fairness pairs
data/             local SQLite database and uploaded files (git-ignored)
docs/             analysis, cost model, ADRs, decision log, evaluation reports
```

## Data and privacy in this build

- Everything stays in `data/` on this machine. The API binds to 127.0.0.1 and has no login, so do not expose it to a network.
- Erasing a candidate deletes their file, evidence, vectors, results and feedback, then verifies that nothing is left. The result is recorded in the audit log.
- CV text is treated as untrusted data. Hidden text is removed, and injection attempts are flagged and never followed.
- Only process CVs you are allowed to use. This project never scrapes LinkedIn, Naukri or any other site (ADR 0004).

## Known limits (deliberate for the initial build)

- No authentication or row-level security; tenant is a header (`X-Tenant-ID`) for local testing.
- SQLite + numpy vectors + FTS5 instead of Postgres + pgvector + BM25. In-process background tasks instead of a queue.
- No OCR. Scanned PDFs are marked *failed* with a clear reason.
- Offline embeddings are a concept-hash, not a neural model. Use `NSM_EMBEDDING_MODE=openrouter` for real embeddings.
- The OpenRouter path is covered by tests with a fake client. Run a small real batch, and watch the cost page, before a larger one.
