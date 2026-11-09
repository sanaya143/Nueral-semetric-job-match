# Open Questions

## Answered (2026-11-05)

| # | Question | Answer | Recorded as |
|---|---|---|---|
| Q-001 | First customer segment | IT services/staffing talent rediscovery, India | D-013 |
| Q-002 | Geography and data residency | India first, EU-ready | D-014 |
| Q-003 | Source of the gold evaluation set | Design partner (consented or anonymized CVs plus recruiter grades) | D-015 |
| Q-004 | Hardware | No GPU: API embeddings and rerank first | D-016 |
| Q-005 | Team | Solo developer, strong in Python; has used LangGraph/LangChain and React + TS; new to Celery/Redis and Postgres/pgvector | D-010, D-017 |
| Q-006 | Cost targets | Production ≤ $0.20/run and ≤ $0.003/CV; development cap $50/month | D-018, D-019 |

## Follow-ups created by the answers

| # | Action | Owner | Needed by |
|---|---|---|---|
| F-001 | Find 1–2 design partners (IT services/staffing firms, India). Prepare a consent and DPA template and an anonymization procedure for shared CVs | You (I can draft the templates) | Start of Phase 2 gold-set work |
| F-002 | Choose embedding and rerank API providers: DPA plus no-retention terms, embedding price ≤ $0.05/M tokens, quality checked in the bake-off. Confirm where they process data | Phase 2 | Before ingesting partner CVs |
| F-003 | Run a 2–3 day Postgres + pgvector + hybrid-search learning spike, since both are new to you. Use managed Postgres to avoid running the database yourself | Phase 2 | Before Phase 3 schema design |
| F-004 | Re-plan the timeline for a solo developer. Phases 2–7 were estimated at 12–17 weeks for 1–2 developers; plan for the upper end (~4–5 months to a pilot) | Phase 2 | Phase 2 kickoff |

## Assumptions to validate in Phase 2

| # | Assumption | How to validate |
|---|---|---|
| A-001 | Current per-model prices on OpenRouter match what the cost model uses | Pull live prices from the OpenRouter models API on the day of the bake-off |
| A-002 | ~~It is unconfirmed whether OpenRouter passes through provider batch APIs (~50% off)~~ **Resolved 2026-11-05:** OpenRouter's model list includes `:batch` variants at 50% of the list price. The gateway supports them for bulk ingestion (`NSM_USE_BATCH_VARIANTS`). Still to measure: batch turnaround time on a real bulk upload | Measure turnaround on the first real bulk upload |
| A-003 | Prompt caching works through OpenRouter for the chosen models, and our rubric prefix meets each model's minimum cacheable length | Measure cached-token counts in responses |
| A-004 | Reasoning/thinking tokens stay small for extraction and verification at low effort | Measure output plus reasoning tokens per call in the bake-off |
| A-005 | An average CV is about 2K tokens; extraction output about 1.5K | Measure on the gold-set CVs |
| A-006 | Unified ATS API pricing (Merge/Kombo/Knit-type) is acceptable | Request quotes |
| A-007 | LinkedIn partner eligibility; possibility of Naukri RMS integration | Submit the partner form; contact Info Edge |
| A-008 | Using Resdex-sourced CVs uploaded by customers is permitted under their licence | Legal review |
| A-009 | ParadeDB `pg_search` is available on the chosen managed Postgres in an Indian region | Check provider extension lists |
| A-010 | Latency SLOs: matchable within 2 min of ingestion; match run p95 < 20 s | Load test in Phase 7 |
| A-011 | Competitor positioning (LinkedIn AI tools, Workday/HiredScore, Eightfold, SeekOut, hireEZ, Textkernel) | Desk research plus customer interviews |
| A-012 | Sending redacted CV text to LLM and embedding APIs outside India is acceptable under DPDP cross-border rules and under design partners' contracts | Legal check of DPDP transfer provisions plus partner DPA review |
| A-013 | The MVP configuration stays under the $0.003-per-CV cap with real token counts (current estimate $0.0030, at the cap) | Measure in the bake-off; if over, cache the extraction prompt or use a cheaper embedding API |
