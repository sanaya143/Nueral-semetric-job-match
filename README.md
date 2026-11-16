# Neural-Semantic Matching: initial local build

Ranks candidates by **evidence of work**, not keyword density. Each CV is split into evidence units (roles and projects). Each job becomes requirements you confirm (must / nice-to-have / learnable). Every verdict quotes the CV, and a deterministic scorer computes the ranking. The recruiter makes the decision.

By default it runs **locally for one user**: **SQLite + local files**, **offline at $0**, with an optional **OpenRouter mode**. Version 1.2 adds what a hosted deployment needs: **PostgreSQL** (Azure Database for PostgreSQL), **workspace keys or Entra ID sign-in**, **rate limits and daily quotas**, a **readiness check**, and **OCR** for scanned PDFs and image CVs. See [Production settings](#production-settings-v12) and `CHANGES.md`.

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

## Easiest way to run it on Windows

1. Install Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node.js 20+.
2. Double-click **`setup.bat`** once. It installs everything, builds the dashboard and loads the demo data.
3. Double-click **`run.bat`**. It starts the app and opens http://127.0.0.1:8000.
4. Optional: double-click **`test.bat`** to run the tests and the evaluation.

## Quick start (offline mode, $0, no key)

Run each command from the project root (the folder that holds this README).

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

**No model is built in.** You choose the models in `.env`, and OpenRouter mode stays off until you do.

1. Copy `.env.example` to `.env` in the project root. `.env` is git-ignored.
2. Create a key at https://openrouter.ai/keys. Set a credit limit on it as a second safety net.
3. In `.env`, paste **your** key after `NSM_OPENROUTER_API_KEY=` and set `NSM_LLM_MODE=openrouter`.
4. Choose two models from https://openrouter.ai/models and set them as `NSM_MODEL_SMALL` (high-volume tasks) and `NSM_MODEL_LARGE` (low-volume, harder tasks). Each task can be overridden on its own, for example `NSM_MODEL_DEEP`.
5. Set `NSM_MODEL_PRICES` to their per-1M-token prices from the model pages. Budget checks use these before every call. A model with no price is estimated at $5 / $25 per 1M tokens, which is safe but blocks most calls under the per-CV cap.
6. Restart the API. The sidebar badge changes from *Offline ($0)* to *OpenRouter*, and the **Costs & audit** page lists the model used for each task.

Every request is sent with `data_collection: deny`, zero-data-retention routing, and PII (emails, phone numbers, URLs) scrubbed. Names are already in the vault.

### What runs where

| Action | Model tier | Calls | Hard stop |
|---|---|---|---|
| Ingest one CV | small (large re-labels only the failed sections if >30% of quotes fail validation) | 1, sometimes 2 | `NSM_CAP_PER_CV_HARD_USD` (default $0.024) |
| Parse one JD | large | 1 | monthly cap |
| Match run with LLM verification | small verifies 50; large deep-evaluates 5 | 55 | `NSM_CAP_PER_RUN_USD` (default $0.20); remaining candidates keep offline verdicts |
| Extra deep evaluation (ranks 6–10, on click) | large | 1 | per-run cap |
| Search with "interpret with LLM" | small | 1 | monthly cap |
| API embeddings (`NSM_EMBEDDING_MODE=openrouter`, needs `NSM_EMBEDDING_MODEL`) | embedding model | 1 per CV | monthly cap |

Hidden thinking is switched off on every call (D-024). If a model refuses thinking-off, that call falls back to low effort with a small reserved budget. The **Thinking** column on the Costs page shows which case you are in.

To estimate the cost of your chosen models before spending anything, set them in `.env` and run `scripts/token_profile.py`. It replays the exact prompts at $0 and estimates output tokens. The cost meter records the real counts on your first run.

The monthly cap is $50 (D-019). Each call is checked against its worst-case cost *before* it is sent, and every call is metered on the **Costs & audit** page. Bulk ingestion can use `:batch` model variants at half price (`NSM_USE_BATCH_VARIANTS=true`). Full model: [docs/02-token-cost-model.md](docs/02-token-cost-model.md).

## Everyday commands

Run the backend tests (67):

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
- Because there is no login, the API only answers to `127.0.0.1` / `localhost` (`NSM_ALLOWED_HOSTS`) and refuses writes sent by other websites through your browser (`NSM_ALLOWED_ORIGINS`).
- Hidden text is detected in PDFs (white, very pale, transparent, invisible-mode, tiny, off-page, or covered by a box) and in DOCX (white or near-white, hidden, tiny, including table cells).
- Erasing a candidate deletes their file, evidence, vectors, results and feedback, then verifies that nothing is left. The result is recorded in the audit log.
- CV text is treated as untrusted data. Hidden text is removed, and injection attempts are flagged and never followed.
- Only process CVs you are allowed to use. This project never scrapes LinkedIn, Naukri or any other site (ADR 0004).

## Production settings (v1.2)

All of these are environment variables (or `.env` lines); `.env.example` lists them with comments.

**Database.** Leave `NSM_DATABASE_URL` empty for the local SQLite file. For Azure Database for PostgreSQL:

```
NSM_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@SERVER.postgres.database.azure.com:5432/nsm?sslmode=require
```

Tables and the full-text index are created on first start. The whole test suite and the evaluation also pass on
PostgreSQL 16 (`NSM_TEST_DATABASE_URL=<empty test database> uv run pytest -q`).

**Who can use which workspace.** `NSM_AUTH_MODE`:

| Mode | Use it for | How the workspace is chosen |
|---|---|---|
| `none` (default) | your own computer only | the Workspace box in the sidebar (`X-Tenant-ID` header) |
| `api_key` | a hosted pilot | by the key: `python scripts/make_api_key.py <workspace>` prints a key and the `NSM_API_KEYS` line. The browser asks for the key once per tab. |
| `entra` | production with Microsoft sign-in | by a token claim (`NSM_OIDC_TENANT_CLAIM`, default `tid`), optionally mapped with `NSM_OIDC_TENANT_MAP`. Set `NSM_OIDC_ISSUER` and `NSM_OIDC_AUDIENCE`. |

In `api_key` and `entra` modes a request that names a different workspace in `X-Tenant-ID` gets 403, and repeated bad keys from one address get 429.

**Limits.** `NSM_RATE_LIMIT_PER_MINUTE` (300), `NSM_WRITE_RATE_LIMIT_PER_MINUTE` (60), `NSM_DAILY_UPLOAD_QUOTA` (2000 files per workspace per UTC day) and `NSM_DAILY_MATCH_RUN_QUOTA` (200). Over a limit the API answers 429 with `Retry-After`. Rate limits are counted per server instance; daily quotas are counted in the database, so they are exact.

**Health.** `GET /api/v1/health` says the process is up. `GET /api/v1/ready` checks the database, file storage, OCR, sign-in settings and model settings, and answers 503 if any fail. Point the Azure health check at `/api/v1/ready`. Neither needs a key.

**OCR.** `NSM_OCR_ENGINE=auto` (default) uses the bundled local engine (RapidOCR, CPU, offline). Pages with almost no text but a picture are read with OCR, and images (PNG, JPG, TIFF) can be uploaded directly. `azure` uses Azure AI Document Intelligence (`NSM_AZURE_DOCINTEL_ENDPOINT`, `NSM_AZURE_DOCINTEL_KEY`); `off` turns OCR off. CVs read by OCR are flagged so the recruiter checks key quotes against the original.

## Known limits

- In `none` mode there is no login and the workspace is a header, so use it only on your own computer.
- Vectors are still a numpy index held in memory per process (not pgvector). CV processing and match runs are in-process background tasks, not a queue, so a restart loses work in flight.
- No row-level security in PostgreSQL yet; isolation is enforced by the workspace filter on every query.
- Offline embeddings are a concept-hash, not a neural model. Use `NSM_EMBEDDING_MODE=openrouter` for real embeddings.
- The OpenRouter path is covered by tests with a fake client. Run a small real batch, and watch the cost page, before a larger one.
