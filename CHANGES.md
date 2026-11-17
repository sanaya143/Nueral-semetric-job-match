# Changes in version 1.2 (2026-11-10)

Production hardening from the readiness review. The backend now has 67 tests, all passing on SQLite **and on
PostgreSQL 16**. The synthetic evaluation is unchanged on both databases (nDCG@10 0.974, fairness |Δ| 0.0).

## Workspace security (the X-Tenant-ID hole)
- The workspace now comes from the caller's credential, not from a header the caller picks (`backend/nsm/security.py`).
  `NSM_AUTH_MODE=api_key`: each key belongs to one workspace, and only SHA-256 hashes are stored
  (`scripts/make_api_key.py`). `NSM_AUTH_MODE=entra`: Entra ID / OIDC tokens are checked for signature, issuer,
  audience and expiry, and the workspace comes from a claim.
- A request whose `X-Tenant-ID` names another workspace gets 403. Repeated bad keys from one address get 429.
- The dashboard asks for the workspace key once per browser tab and keeps it only for that tab.
- `NSM_AUTH_MODE=none` (the default) keeps the old local behaviour, for your own computer only.

## Rate limits and quotas
- Per-workspace limits on all requests and on writes, and daily quotas for uploaded files and match runs.
  Over a limit the API answers 429 with `Retry-After`.

## Readiness check
- New `GET /api/v1/ready`: checks the database (including the full-text index), file storage, OCR, sign-in
  settings and model settings. It answers 503 when any check fails. `/health` now also reports the auth mode.

## OCR
- Scanned PDF pages are read with OCR, page by page, and PNG, JPG and TIFF CVs can be uploaded
  (`backend/nsm/ingestion/ocr.py`). The default engine is bundled and runs offline at $0. Azure AI Document
  Intelligence is an option. CVs read by OCR carry the flag `ocr_used`.

## PostgreSQL
- Set `NSM_DATABASE_URL` to a `postgresql+psycopg://` URL (for example Azure Database for PostgreSQL). Tables
  and a tsvector full-text index (in place of SQLite FTS5) are created on first start. Connections are
  pre-pinged and recycled.

## Still open
Vector index in memory per process (pgvector next), in-process background tasks (queue next), no row-level
security yet, and review items 3–6 and 8–11.

# Changes in version 1.1 (2026-11-10)

All changes come from the version 1 review. Each loophole has a regression test in
`backend/tests/test_loopholes.py` that fails on version 1 and passes now. The backend has 56 tests, all passing.
The synthetic evaluation is unchanged (nDCG@10 0.974, fairness |Δ| 0.0).

## Dashboard and candidate list
- Dashboard stat cards are now links: **Candidates ready** opens the ready list, **Total profiles** opens all
  candidates, **Jobs** opens jobs, and **Model spend** opens Costs & audit.
- The candidate **Find** box searches skills, evidence text, headline, location and file name, and needs every
  word to match. It used to search only the headline, so "RAG engineer" found nothing. Names are left out on
  purpose, so blind review still holds.
- Filters are kept in the URL (`/candidates?status=ready&q=pgvector`), so you can link to them.
- Fixed a race where a slow, older response overwrote newer filter results (`frontend/src/hooks.ts`).
- When filters match nothing, the list says so, instead of "No candidates yet".

## Loophole 1: skill stuffing
- A sentence that names more than 5 skills no longer makes them all "demonstrated". They count as "applied",
  and the CV gets the flag `skill_dense_sentences` (`extraction/heuristic.py`).
- If more than 3 requirements are "demonstrated" by the same quote, each is scored as applied. Confidence also
  drops, and the report adds an interview check (`scoring/scorer.py`).
- Result on the attack CV: it went from #1 (score 100, high confidence) to #4 (71.2, low confidence).

## Loophole 2: hidden text
- PDF text is now treated as hidden when it is invisible (render mode 3), transparent, very pale (#D9D9D9 or
  lighter), or covered by an opaque box drawn on top of it. Normal grey text and text on coloured panels are kept.
- DOCX text is now treated as hidden when it is near-white (not only pure #FFFFFF), and table cells are checked too.

## Loophole 7: browser attacks on the local API
- The API refuses requests whose Host header is not `127.0.0.1` or `localhost`, which blocks DNS rebinding
  (setting: `NSM_ALLOWED_HOSTS`).
- The API refuses POST, PUT, PATCH and DELETE requests sent by other websites, which blocks CSRF uploads.
  Same-origin requests, the Vite dev origin, and requests with no Origin header (curl, scripts) are still allowed
  (setting: `NSM_ALLOWED_ORIGINS`).

## No built-in model
- The code, config, UI and README name no vendor model. You choose models in `.env`: `NSM_MODEL_SMALL`,
  `NSM_MODEL_LARGE`, optional per-task overrides, `NSM_EMBEDDING_MODEL`, and their prices in `NSM_MODEL_PRICES`.
- OpenRouter mode stays off until both model tiers are set, and the Costs page says which ones are missing.
- Prompt-cache hints are a setting (`NSM_EXPLICIT_CACHE_CONTROL`), not a check on the vendor name.
- `docs/` (strategy, decision log, cost model, token profile) are historical records and are unchanged.
  `skills.json` still lists "claude api" as a CV skill alias, because it describes candidates' skills, not this app.

## Not fixed yet (from the review)
3 and 4 (injection filter bypass: zero-width characters, look-alike letters, rewording), 5 (dates are not
sanity-checked), 6 (names and personal details that are not on the first line), 8 (a bad filter value crashes the
run instead of returning 400), 9 (near-duplicate detection), 10 (one large file rejects a whole ZIP), 11 (feedback
for a candidate who is not in the run).
