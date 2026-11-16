# Neural-Semantic Matching Protocol: Phase 1 Strategic & Technical Analysis

Status: approved 2026-11-05 · Date: 2026-11-05 · No implementation code in this phase.
Tags used below: **[F]** confirmed fact (source at the end), **[R]** my recommendation, **[A]** assumption that still needs checking.

---

## 0. Context

You want to build a recruitment intelligence platform. It should rank candidates by the capability they have *demonstrated*, not by how often keywords appear in their CV, and every ranking should come with evidence. The working folder `D:\Learning\Project1` is empty, so this is a new build. This document answers the 15 questions from the brief and gives the 8 requested deliverables. It also pushes back where I think the brief's assumptions are wrong. On approval, it becomes the project's founding design record (see §19).

---

## 1. What we are actually building (reframed)

**Not** a tool that "discovers candidates on LinkedIn/Naukri". That framing rests on data access we cannot legally obtain at scale (§5).

**What it should be [R]:** an **evidence-grounded matching layer** that sits on top of candidate data an organization is already allowed to use: its ATS, its applicants, its CV archive, candidate-consented imports, and partner APIs where it gets approved. It:
1. Converts every source into one canonical candidate representation (interoperability).
2. Breaks each CV into *evidence units* (things the person actually did) and links them to canonical competencies.
3. Breaks each job into *requirement atoms*, each marked must-have, nice-to-have, or learnable.
4. Matches requirement to evidence through a cost-tiered funnel: hybrid retrieval → rerank → LLM verification on a shortlist only.
5. Produces a ranking you can audit: per-requirement verdicts that cite the CV text, explicit gaps, and a confidence level. A deterministic scorer computes the final number. The LLM does not.

**Recommended first market (wedge) [R]: talent rediscovery for IT services and staffing firms (India first).** These firms hold tens of thousands of dormant CVs in an ATS or in folders, and keyword search fails on them. The data is already theirs to use, the pain is easy to measure (re-sourcing cost, time-to-submit), and tech roles are a domain where we can build a good skills taxonomy and evaluation set. Validate this with 5–10 discovery interviews during Phase 2.

---

## 2. Is it feasible?

| Dimension | Verdict |
|---|---|
| Semantic understanding of CVs/JDs | **Feasible today.** Small LLMs do structured extraction well and cheaply. Embeddings plus cross-encoder rerankers are mature technology. |
| Telling demonstrated from claimed skills | **Feasible, with honest limits.** We can measure how strongly the CV supports a claim. We **cannot** tell whether a claim is true. Present this as "evidence strength", never "lie detection". |
| Cost at 1M candidates | **Feasible**, if the funnel is respected (§12). Naive per-CV LLM scoring is not. |
| Access to LinkedIn/Naukri candidate data | **The binding constraint.** Partner-gated or simply unavailable (§5). |
| Regulatory | **Feasible, but it shapes the design.** Recruitment AI is "high-risk" under the EU AI Act. India's DPDP obligations phase in through about May 2027 (§14). |

---

## 3. How we differ from a traditional ATS

| Traditional ATS | This engine |
|---|---|
| Keyword/Boolean match on the whole CV | Requirement-atom ↔ evidence-unit matching |
| Job title counts as a proxy for skill | Titles are context, never evidence of a capability |
| Skills list = skills | Skills list = "listed-only", the weakest evidence level |
| One opaque score | Multi-dimensional scores plus must-have gating plus confidence |
| No explanation | Every verdict cites CV text spans that a validator has checked |
| Synonyms missed ("document retrieval pipeline" ≠ "RAG") | Taxonomy plus embeddings map terminology to competencies |

**Worked example (your Candidate A vs B):**

JD requirement atoms: R1 Python (must) · R2 LLM application development (must) · R3 RAG (must) · R4 vector DB (must) · R5 production deployment (must). The title "AI Engineer" is *not* turned into a requirement.

| Atom | Candidate A ("AI Engineer"; skills: AI, ML, Python) | Candidate B (never says "RAG" or "AI Engineer") |
|---|---|---|
| R1 Python | Listed-only | Applied: "Python backend services" |
| R2 LLM apps | Listed-only ("AI") | **Demonstrated:** "built LLM-powered document Q&A assistant" |
| R3 RAG | Missing | **Demonstrated** (terminology mapped): "implemented document retrieval pipeline feeding the LLM" |
| R4 Vector DB | Missing | **Demonstrated:** "integrated pgvector store" |
| R5 Deployment | Missing | **Demonstrated:** "deployed to production, 300 internal users" |
| **Outcome** | Fails must-have gate (0/5 demonstrated) | Ranked top, high confidence |

Note the nuance: for B, the *term* "RAG" is mapped, but the *capability* is demonstrated by described work. That differs from an *inferred capability*, which the system always labels as inferred and gives low weight.

---

## 4. Understanding capability instead of keywords: the Evidence–Capability Model [R]

**Evidence levels (fixed vocabulary, used everywhere):**
- **Demonstrated:** the CV describes the person doing the work (action + object + context or outcome).
- **Applied:** a technology is named inside a described role or project, without detail on what they did with it.
- **Inferred:** not stated, but strongly implied by adjacent demonstrated work (e.g. built FastAPI services → REST design). Always labelled, low weight, never presented as fact.
- **Listed-only:** appears only in a skills list or summary.
- **Missing.**

**Unsupported-claim flags (shown to the recruiter, never used to accuse the candidate):** "Expert in X" with no supporting evidence; a seniority title that doesn't fit the stated tenure; overlapping or impossible dates. The UI shows "needs verification". These flags do not lower the score beyond the absence of evidence itself.

**Candidate representation (not one vector per CV):**
1. **Structured facts** (Postgres): roles, dates, employers, projects, education, stated preferences. Preferences come only from explicit statements.
2. **Evidence units:** each role, project, or significant group of bullets, stored with a context header ("Backend engineer, fintech, 2022–24: …"). Each has a dense vector plus a sparse/lexical representation, plus character offsets back into the source text.
3. **Capability claims:** edges from an evidence unit to a canonical competency, each carrying an evidence level and a confidence.
4. **Profile summary vector** for cheap coarse recall.

**Knowledge graph decision [R]:** for the MVP, use a **relational capability graph in Postgres**, *not* a graph database. Tables: candidate → evidence_unit → capability_claim → skill, plus skill_relation (broader, narrower, related, implies). Recursive CTEs handle the 2–3 hops we need. Seed the taxonomy from **ESCO** (free, multilingual) and **O*NET**, and add a hand-curated tech/AI layer. Move to Neo4j or similar only when multi-hop analytics (career paths, workforce intelligence) become a core product.

**Interoperability standards [R]:** model the canonical schema on **HR Open Standards** (recruiting JSON schemas) and **JSON Resume**, and represent jobs with **schema.org/JobPosting**. That makes import and export to other systems straightforward.

---

## 5. Legitimate data sourcing (LinkedIn, Naukri, others)

| Source | Access reality | Our approach [R] |
|---|---|---|
| **LinkedIn** | **[F]** No open candidate API. Talent Solutions access requires an approved partnership (form-based application) and per-customer contract identifiers. **Apply Connect** sends applicant profile data to a *partner ATS* when someone applies to that customer's job, and LinkedIn states it may not be available to everyone. **RSC** syncs a limited set of fields (name, email, ATS ID, timestamp) between an ATS and LinkedIn Recruiter. Scraping is prohibited by LinkedIn's User Agreement and has been litigated. | MVP does **not** depend on LinkedIn. We accept **candidate-provided** LinkedIn data, e.g. a profile PDF export the candidate uploads with consent. Apply for partner status once we function as an ATS/CRM layer with paying customers. **Never scrape.** |
| **Naukri (Info Edge)** | **[F]** I found no public developer API. Naukri RMS (an ATS) and Resdex (a paid recruiter database) are per-seat products. Third-party "Naukri API" offerings rely on scraping, which we should avoid. | Customers upload CVs they legitimately downloaded under their own Resdex licence; the customer is the controller/fiduciary and we are the processor, under a DPA. Approach Info Edge about an RMS integration partnership. **[A]** Licence terms on bulk export need legal review. |
| **ATSs** (Greenhouse, Lever, Workday, SmartRecruiters, Zoho Recruit, …) | Most expose documented APIs with customer-granted keys or OAuth. | After the MVP: one **unified ATS API** (Merge / Kombo / Knit-type vendors) covers many ATSs with a single integration. **[A]** Vendor per-connection pricing could exceed our LLM costs; verify it. |
| **Own apply flows / career pages** | The candidate consents by applying. | Hosted apply link or embeddable widget, plus webhooks. |
| **Uploaded CVs / internal databases** | Customer-controlled data. | **Primary MVP source.** |
| **Public profiles** (GitHub, Kaggle, papers) | Public, but still personal data, and each site has its own terms. | Only links the candidate provides, with consent. Phase 8. |

**Connector framework [R]:** each source is a plugin implementing `fetch → map to canonical schema → emit change events`, with idempotent upserts keyed by `(tenant, source, external_id)` and recorded consent/lawful basis. The matching engine never knows which source a record came from.

---

## 6. Where MCP fits, and where it doesn't

MCP's purpose is exposing tools and context **to LLM clients**. Bulk, deterministic data movement belongs in ordinary connectors. **[F]** The current MCP spec revision is dated 2026-07-28; its authorization model is built on OAuth 2.1, with MCP servers acting as resource servers.

| Component | MCP? | Mechanism | Reason |
|---|---|---|---|
| Candidate ingestion from ATSs and job boards | **No** | REST + webhooks via the connector framework | Bulk, deterministic, auditable; no LLM in the loop |
| CV parsing, extraction, matching internals | **No** | In-process / internal API + job queue | Latency, cost, reliability |
| LLM / embedding / rerank providers | **No** | HTTP through our Model Gateway | — |
| **Our platform exposed to AI assistants** (Claude, Copilot, ChatGPT, etc.) | **Yes: outbound remote MCP server** (Phase 8) | OAuth 2.1; tenant taken from the token, never from parameters | A distribution channel: recruiters query the engine from the assistant they already use |
| In-app recruiter assistant reaching customer systems (calendar, email, HRIS) | **Yes: MCP client** (Phase 8+) | Consume MCP servers the customer provides | Customer-specific integrations without bespoke code |
| Engineering/ops analytics | Optional | MCP server over a read replica | Developer productivity |

**Outbound MCP server tool design (Phase 8):** `search_candidates`, `get_match_report`, `list_jobs`, `explain_ranking`, `submit_feedback`. Write actions such as `create_job` require confirmation. Rules: no bulk-export tool; outputs minimize PII; every call is audit-logged; per-tool scopes and rate limits; CV text in outputs is delimited as untrusted content.

---

## 7. Your stack, evaluated

| Layer | Your preference | Verdict [R] | Notes |
|---|---|---|---|
| Frontend | React + TS | **Keep** | Vite, TanStack Query, shadcn/ui (or MUI), OpenAPI-generated client |
| Backend | Python + FastAPI | **Keep** | Pydantic v2, SQLAlchemy 2 (async), Alembic. **Modular monolith**, not microservices |
| LLM gateway | OpenRouter | **Keep for LLM generation, behind our own gateway interface** | See §8. Not the default for embeddings/rerank or bulk batch work |
| Orchestration | LangGraph | **Defer / narrow** | The matching pipeline is a fixed DAG, which plain async functions plus a queue handle. Write steps as "node-shaped" pure functions so LangGraph can wrap them later. Adopt it for the Phase 8 conversational recruiter assistant, or earlier if checkpoint/retry ergonomics prove valuable in Phase 4 |
| Retrieval | RAG + hybrid | **Keep, reframed** | This is retrieval *for ranking*, plus grounded generation for explanations. It is not a chat-RAG system |
| Relational DB | PostgreSQL | **Keep, as the centre** | Row-Level Security for tenant isolation; erasure is one cascading delete |
| Vector store | pgvector or Qdrant | **pgvector for MVP** | HNSW + `halfvec`, iterative filtered scans, BM25 via ParadeDB `pg_search` (or native FTS), fused with RRF in SQL. One store keeps consistency, RLS, and deletes simple. Put a `VectorIndex` interface in front; move to Qdrant past roughly 10–50M vectors or if filtered-latency SLOs slip **[A]** |
| Elasticsearch/OpenSearch, Weaviate | — | **Not now** | Operational weight with no clear benefit at MVP scale |
| Embeddings | — | **Bake-off in Phase 2** | Default candidates: **BGE-M3** (dense + sparse in one model, multilingual, 8K context) and **Qwen3-Embedding-0.6B**, both self-hosted. API alternatives: Gemini Embedding, Voyage, OpenAI text-embedding-3-large |
| Reranking | Specialized rerankers | **Keep** | Self-hosted **bge-reranker-v2-m3** or **Qwen3-Reranker-0.6B**; API alternatives Cohere Rerank or Voyage rerank |
| Background jobs | Celery + Redis | **Replace for MVP with a Postgres-backed queue (Procrastinate)** | Async-native, and it leaves one stateful dependency instead of two. Move to Celery/Redis or Temporal (durable workflows) when throughput demands it. Celery remains an acceptable choice if you already know it well |
| Doc parsing | — | **Docling / PyMuPDF / python-docx; Tesseract OCR** | Deterministic and free. LLM vision only as a fallback for unparseable files |
| Observability | — | **OpenTelemetry + Langfuse** (self-hostable) | Tracing, plus token and cost per call, per job, per tenant |
| Deployment | Docker + cloud | **Keep** | Docker Compose for local dev (Docker Desktop + WSL2 on your Windows machine). For the MVP: managed Postgres plus a serverless container platform, in the region where the data must live (e.g. Mumbai for Indian customers). No Kubernetes before scale |

---

## 8. Is OpenRouter the right primary gateway?

**Yes for LLM generation, with conditions.**
- **Pros [F]:** one OpenAI-compatible API over hundreds of models; provider pricing is passed through with no markup on inference; provider routing and fallbacks; ZDR (zero-data-retention) routing is available; it now also lists embedding and rerank models.
- **Costs and risks [F]:** a **5.5% fee on credit purchases**; an extra network hop (added latency); an additional sub-processor handling CV personal data; data collection is *allowed* by default and must be set to `deny` per request or account; actual retention depends on which downstream provider handles the request.
- **[A]** I could not confirm whether OpenRouter passes through providers' asynchronous **batch APIs**, which are typically about 50% cheaper. If it doesn't, bulk ingestion should go direct to the provider.

**Design [R]:** our own **Model Gateway** module owns task → model routing, fallbacks, retries, budgets, cost metering, response caching, and PII redaction. OpenRouter is one backend; direct provider APIs and self-hosted models are others. Every request through OpenRouter carries ZDR + `data_collection: deny` + a provider allowlist. Swapping a model is a config change.

---

## 9. Initial model selection strategy (task-based routing)

Prices are list prices per 1M tokens (input/output). Claude prices are Anthropic list prices **[F]**. Other prices come from aggregators and are **indicative only [A]**.

| Task | Volume | Tier | Primary (to confirm in bake-off) | Fallback | Why |
|---|---|---|---|---|---|
| PDF/DOCX → text | every CV | none | Docling / PyMuPDF; Tesseract OCR | LLM vision (rare) | Deterministic, free |
| CV structured extraction + evidence units | every CV | small | Claude Haiku 5.5 ($0.10/$0.50) vs GPT-5 Nano (~$0.05/$0.40) vs Qwen3.x Flash / DeepSeek V4.x Flash (~$0.03–0.05 in) | A small model from another provider; **escalate low-confidence cases to mid tier** | High volume, schema-bounded |
| Skill normalization | every claim | embedding + rules | Nearest neighbour against the taxonomy | Small LLM for unresolved terms, **cached globally** (non-PII) | Nearly free after warm-up |
| JD → requirement atoms | per job | mid | Claude Sonnet 5.5 ($2/$10) | Another provider's mid tier | Low volume, high leverage: every downstream decision depends on it |
| Embeddings | every evidence unit | specialized | BGE-M3 / Qwen3-Embedding-0.6B self-hosted | API embedding | Volume, latency, PII, cost |
| Rerank | per job × ~300 | specialized | bge-reranker-v2-m3 / Qwen3-Reranker-0.6B | Cohere / Voyage rerank | Cross-encoder precision |
| Evidence verification (top ~50) | per job | small | Same as extraction model | Mid | Bounded, structured |
| Deep evaluation & comparison (top ~10) | per job | mid | Sonnet 5.5 | Another mid tier | Nuanced trade-offs |
| Recruiter report narrative | on view | small / template | Mostly templated from verified verdicts | Template only | Text is derived from verdicts, not invented |
| Offline judge / gold-set labelling assist | eval only | frontier | Claude Opus 5.5 ($4/$20) | — | Quality reference; **never on the production path** |

Rule: any model change ships only after it passes the Phase 2 evaluation set.

---

## 10. Hybrid retrieval and reranking funnel

```
JD -> requirement atoms (must / nice / learnable, weights, skill IDs, "evidence-looks-like" text)
 Stage 0  Hard filters (only recruiter-set, job-related: location / remote / work auth / availability)
 Stage 1  Per-atom hybrid retrieval over EVIDENCE UNITS                       ~ms,  $0
          dense ANN (pgvector) + BM25 (pg_search) -> RRF fusion
          aggregate per candidate: best evidence per atom -> coverage vector -> top ~300
 Stage 2  Cross-encoder rerank of (atom, evidence) pairs                       ~1-3 s
          -> top ~50
 Stage 3  Small-LLM verification: per-atom verdict + cited evidence-unit IDs   ~$0.03/job
          -> top ~10-20
 Stage 4  Mid-LLM deep evaluation + comparative notes                          ~$0.15/job
 Scorer   DETERMINISTIC aggregation -> dimension scores, gates, confidence -> ranked list
```

- **Progressive UX:** the Stage 1–2 ranking appears in about 1–3 s, then rows refine as LLM verdicts stream in. "Real-time" means new candidates are matchable within about 2 minutes of ingestion and match runs finish p95 under about 20 s **[A, set as SLOs in Phase 3]**.
- **Incremental matching:** a newly ingested candidate passes through Stages 1–2 against open jobs. The LLM runs only if they reach the top K.
- **Natural-language search** reuses the JD pipeline: query → atoms → the same funnel.

---

## 11. Scoring and explainability framework [R]

**Evidence weights:** Demonstrated 1.0 · Applied 0.7 · Inferred 0.4 (shown separately) · Listed-only 0.2 · Missing 0. These are adjusted by recency and specificity.

**Dimensions (default weights, configurable per job):**

| Dimension | Default | Inputs |
|---|---|---|
| Must-have coverage | **Gate** (configurable: e.g. all must-haves ≥ Applied) | Atom verdicts |
| Technical competency | 30% | Evidence strength on technical atoms |
| Practical experience (depth, scope, recency) | 25% | Evidence units, durations |
| Project / domain relevance | 15% | Domain atoms |
| Nice-to-have coverage | 10% | Atom verdicts |
| Seniority / experience fit | 10% | Structured facts vs the job's level |
| Logistics compatibility | 10% | **Only explicit** candidate statements |
| Education / certifications | **0% unless the job requires them** | Fairness default |

**Confidence** = f(number of evidence items, specificity, recency, extraction confidence, verifier agreement).

**Anti-hallucination by construction:**
1. The LLM may cite only evidence-unit IDs that exist for that candidate.
2. Every quoted span must be an exact substring of the source text; a deterministic validator rejects anything else.
3. The LLM outputs verdicts, not the final score.
4. Every result stores the model and prompt versions, so it can be reproduced.

**Per-candidate report contains:** match summary · per-requirement table (verdict, evidence quote, source location) · demonstrated vs inferred capabilities (visually distinct) · missing and partially met requirements · concerns / "needs verification" flags · confidence · overall suitability band. No hire/reject recommendation. The recruiter decides.

---

## 12. Token and infrastructure cost model

> **Superseded in detail by [02-token-cost-model.md](02-token-cost-model.md)**: per-step breakdown, sensitivity, budget policy. Recommended target there: ~$0.0026/CV and ~$0.16/match run.

**Assumptions [A]:** average CV ≈ 2K tokens; extraction prompt ≈ 1.5K (cached); extraction output ≈ 1.5K; 10% of CVs escalated to mid tier; ≈ 3K embedded tokens per CV; 5% of profiles change per month; OpenRouter fee +5.5%. Prices: Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10 per 1M tokens **[F, Anthropic list, Oct 2026]**.

**Per-unit costs:**

| Operation | Calculation | Cost |
|---|---|---|
| Extraction, small model | 3.5K × $0.10/M + 1.5K × $0.50/M | ≈ $0.0011 |
| Escalation (10% of CVs, mid tier) | (3.5K × $2 + 1.5K × $10)/M × 0.1 | ≈ $0.0022 |
| Embeddings | self-hosted ≈ $0; API ≤ $0.15/M × 3K | ≤ $0.0005 |
| **Per new CV (one-time)** | incl. fee | **≈ $0.004** |
| JD intake (mid tier) | 4K in / 2K out | ≈ $0.03 |
| Rerank ~300 pairs | self-hosted / API | $0–0.01 |
| Stage 3: 50 candidates (small, rubric cached) | ~4K in / 0.6K out each | ≈ $0.03 |
| Stage 4: 10 candidates (mid) | ~4K in / 1K out each | ≈ $0.15 |
| **Per job match run** | | **≈ $0.20–0.30, roughly flat with corpus size** |

**At scale:**

| Candidates | One-time ingestion | Monthly refresh (5%) | Per job run | Naive: mid-tier LLM on every CV per job (~$0.018/CV) | Infra / month (ballpark [A]) |
|---|---|---|---|---|---|
| 1K | ~$4 | ~$0.20 | ~$0.25 | ~$18 | $50–150 |
| 10K | ~$40 | ~$2 | ~$0.25 | ~$180 | $100–250 |
| 100K | ~$400 | ~$20 | ~$0.25–0.30 | ~$1,800 | $300–800 |
| 1M | ~$4,000 (≈$2K with batch APIs or a cheaper validated model) | ~$200 | ~$0.30 | ~$18,000 | $1,500–4,000 |

**Key insight:** once the funnel is in place, **infrastructure costs more than LLM tokens.** Example: 100K candidates and 400 match runs a month comes to about $110 in LLM spend versus $300–800 for infrastructure. So keeping the stack to a single Postgres matters more than squeezing model prices.

**1M-scale storage:** about 15 evidence vectors × 1,024 dims × 2 bytes (halfvec) ≈ 31 GB, plus HNSW overhead. Matryoshka truncation to 512 dims or quantization halves it. That is the point at which pgvectorscale DiskANN or Qdrant earns its place.

**Hidden costs to budget:**
- Human labelling of the gold evaluation set (the largest real cost).
- Unified ATS API vendor fees.
- LinkedIn partner fees.
- OCR for scanned CVs.
- Evaluation runs.
- Re-embedding when the embedding model changes (1M CVs ≈ 3B tokens ≈ $60–450 via API).
- Log storage (and the PII in it).

**Optimization levers, in priority order:**
1. Parse deterministically; use LLMs only for semantics.
2. Extract once, keyed by content hash; on update, re-extract only the changed sections.
3. Deduplicate before extraction (exact hash, MinHash near-duplicates, identity resolution).
4. Send compact evidence packs, not whole CVs (about 50–60% fewer tokens).
5. Keep prompt prefixes stable (rubric first, candidate last) so caching works.
6. Short enum-based structured outputs with capped `max_tokens`.
7. Use provider batch APIs for bulk ingestion.
8. Adaptive top K (stop early when the score gap is large).
9. Cache results by (job_version, candidate_version, model_version).
10. Per-tenant and per-job budget guards, plus a cost meter on every call.
11. Later: fine-tune a small open model on validated extraction outputs.

---

## 13. MVP scope

**In the MVP:**
1. CV ingestion: bulk upload (PDF/DOCX/ZIP) and CSV import; deduplication; hidden-text and prompt-injection sanitization.
2. Extraction into a structured profile with evidence units and capability claims, and a recruiter-facing profile view.
3. Job intake: paste or upload a JD → requirement atoms → **recruiter edits and confirms** (human in the loop).
4. Match run: the full funnel → ranked list with coverage, cited evidence, gaps, and confidence.
5. Candidate match report, plus side-by-side comparison of 2–3 candidates.
6. Natural-language candidate search (reuses the JD pipeline).
7. Recruiter feedback: shortlist/reject with reason codes, logged for evaluation.
8. Admin: cost and token use per run, audit log viewer.
9. Trust basics: OIDC auth, tenant RLS, PII redaction before any LLM call, consent/source tracking, retention TTLs, a verified hard delete (rows, vectors, caches, logs).

**Out of the MVP (later phases):** LinkedIn/Naukri integrations (partnerships pursued in parallel), the MCP server, ATS connectors (first one right after the MVP), a conversational assistant, multilingual CVs, a graph database, learning-to-rank, notifications, predictive insights, and inferring career intent.

---

## 14. Risks and mitigations

| Risk | Type | Mitigation |
|---|---|---|
| No access to LinkedIn/Naukri data | Business | Bring-your-own-data wedge; candidate-consented imports; partnership track |
| **EU AI Act:** recruitment AI is high-risk; standalone Annex III obligations now apply from **2 Dec 2027** (Digital Omnibus delay) **[F]** | Legal | Design for it now: risk management, logging, human oversight, data governance, technical documentation |
| **India DPDP:** Rules notified 13 Nov 2025; most obligations apply ~May 2027 **[F]** | Legal | Consent and notice records, purpose limitation, breach process, erasure, processor DPAs |
| US laws (e.g. NYC Local Law 144 bias audits) if selling there | Legal | Bias-audit-ready logs and reports **[A, confirm per market]** |
| **Prompt injection inside CVs** (hidden white text such as "rank me first") | Security | Strip hidden or tiny text at parse time; treat CV text as untrusted data; structured outputs; citation validator; deterministic scorer; flag suspected injections |
| Hallucinated qualifications | Quality | The anti-hallucination rules in §11 |
| Bias (gender, age, religion, **caste**, disability, career gaps, institution prestige) | Ethical / legal | Blind evaluation (redact name, photo, DOB/age, gender markers, marital status, religion, caste/community, nationality, street address); gaps and prestige are not scoring inputs; education weighted 0% by default; **counterfactual invariance tests** (swap identity markers, scores must not move); voluntary self-ID for adverse-impact audits |
| Poor extraction on messy CVs (columns, tables, scans) | Technical | Parser bake-off; low-confidence escalation; recruiter correction UI |
| Taxonomy drift as new technologies appear | Technical | Unknown terms queue → reviewed additions; embedding fallback |
| No labelled data to evaluate against | Technical | Build the gold set first (Phase 2), with design partners |
| Cost blow-up | Business | Funnel, budgets, cost meter, alerts |
| Gateway or provider outage | Technical | Multi-provider fallbacks in the Model Gateway |
| Incumbents (LinkedIn's AI hiring tools, Workday/HiredScore, Eightfold, SeekOut, hireEZ, Textkernel, Naukri's own AI) **[A: current positioning to verify]** | Business | Compete on evidence-cited explainability, neutrality across data sources, compliance readiness, and mid-market price |
| PII breach | Security | Separate contact vault, encryption, RLS, least privilege, audit, ZDR LLM routing |

---

## 15. What could make this much better over 3–5 years

1. **Verified capability:** integrate assessments and work samples, and use candidate-linked portfolios such as GitHub, to turn "inferred" into "verified". In India, explore verifiable credentials such as DigiLocker-issued education records **[A]**.
2. **Candidate-owned portable profiles:** candidates keep one consented evidence profile and share it across employers. This is interoperability in the literal sense.
3. **Learning-to-rank from recruiter feedback and hiring outcomes,** debiased and validated against the fairness test suite.
4. **Recruiter assistant via MCP** inside the assistants recruiters already use.
5. **Internal mobility and workforce intelligence:** the same engine, with employees as the candidates.
6. **Multilingual and voice-captured profiles** for blue-collar and regional markets.
7. **Compliance as a product feature:** auto-generated AI Act technical documentation and bias-audit reports.
8. **Distilled in-house models** to reduce cost and cover data-residency requirements.

---

## 16. Diagrams

### 16.1 High-level system architecture

```
                 +--------------------------------------------------+
                 |  Recruiter Dashboard (React + TS)                |
                 +-------------------------+------------------------+
                                           | HTTPS, OIDC/JWT
+------------------------------------------v-------------------------------------------+
| FastAPI modular monolith                                                             |
|  auth/tenancy | jobs | candidates | matching | search | feedback | admin/cost | audit |
+-----+------------------+----------------------+------------------+-------------------+
      | enqueue          | SQL (RLS)            |                  | (Phase 8)
+-----v-------------+  +-v----------------------v-----+     +------v-----------------+
| Workers           |  | PostgreSQL                   |     | MCP server (outbound)  |
|  ingest / parse   |  |  relational + capability     |     | OAuth 2.1, read-mostly |
|  sanitize / dedup |  |  graph + pgvector + BM25     |     +------------------------+
|  extract / embed  |  |  queue + audit + cost meter  |
|  match-run        |  +------------------------------+     +------------------------+
+-----+-------------+                                       | Object storage         |
      |                                                     | raw CVs (encrypted)    |
+-----v-----------------------------------------------+     +------------------------+
| Model Gateway: routing, fallbacks, budgets, caching,|
| PII redaction, cost metering                        |
+-----------+------------------+----------------------+
| OpenRouter|  Direct provider |  Self-hosted         |
| (LLMs,ZDR)|  (batch, DPA)    |  embeddings + rerank |
+-----------+------------------+----------------------+

Connectors (NOT MCP): upload/ZIP | CSV | apply-flow webhooks | ATS (unified API) |
LinkedIn partner APIs (if approved) | Naukri RMS (if partnered)  --> Canonical Candidate Schema
```

### 16.2 Data flow: ingestion

```
Source -> Connector -> raw store (encrypted) -> parse (Docling/PyMuPDF/OCR)
 -> sanitize (hidden text, injection flags) -> split PII (contact vault | evaluable text)
 -> dedup (hash, MinHash, identity resolution) -> small-LLM extraction (structured)
 -> validate (schema + span grounding) -> normalize skills (taxonomy NN + rules)
 -> capability claims (evidence level + confidence) -> embed evidence units (dense + sparse)
 -> Postgres (facts, units, claims, vectors) -> "matchable" event -> incremental match vs open jobs
```

### 16.3 Data flow: matching

```
JD -> mid-LLM requirement extraction -> recruiter confirms/edits (HITL) -> requirement atoms
 -> Stage 0 filters -> Stage 1 hybrid retrieval (per atom, RRF) -> Stage 2 rerank
 -> Stage 3 small-LLM verification (cited) -> Stage 4 mid-LLM deep eval (top 10)
 -> deterministic scorer -> ranked list + reports -> recruiter feedback -> eval set / future LTR
```

---

## 17. Phased execution plan (with exit gates)

Durations are indicative for 1–2 developers **[A]**. Phases 4–6 run as iterative milestones gated by evaluation, not as a waterfall.

| Phase | Duration | Key outputs | Exit gate |
|---|---|---|---|
| **1 Analysis** | now | This document, ADRs, decision log | Your sign-off on §18 decisions |
| **2 Feasibility** | 2–3 wks | **Gold eval set v0** (≥20 JDs × ≥40 CVs, graded by recruiters); BM25 keyword baseline; extraction bake-off (3 small models); embedding + reranker bake-off; answers on LinkedIn/Naukri/unified ATS access; compliance checklist; 5–10 customer interviews | Hybrid beats BM25 on recall@50 and nDCG@10 by an agreed margin; extraction field-F1 target met; measured $/CV |
| **3 Architecture** | 1–2 wks | C4 diagrams, ERD, OpenAPI contracts, security/tenancy model, Model Gateway spec, SLOs | Design review |
| **4 Matching engine** | 3–4 wks | Evidence model, funnel, verifier, citation validator, scorer, report schema | precision@10 beats baseline; 0 unvalidated citations; counterfactual-invariance tests pass |
| **5 Cost** | alongside 4 | Cost meter, caching, batch path, budgets | Measured $/job ≤ target |
| **6 MVP build** | 4–6 wks | Backend, workers, React dashboard, upload + first connector | End-to-end on a design partner's real (consented) data |
| **7 Testing & eval** | 2 wks + ongoing | Full eval report: ranking, hallucination, fairness, latency, load, cost vs keyword ATS | Go/no-go for pilot |
| **8 Production & expansion** | ongoing | Deployment, monitoring, MCP server, ATS connectors, multilingual, LTR | — |

---

## 18. Assumptions to validate, and decisions needed from you

> **Answered 2026-11-05.** See [decision-log.md](decision-log.md), D-013 to D-019. Changes since this analysis: LangGraph is now adopted for JD intake and match runs only (ADR 0008, replacing the "defer" verdict in §7); embeddings and reranking use APIs in the MVP (D-016); the job queue is Procrastinate (D-010). Follow-ups are in [open-questions.md](open-questions.md).

**Decisions (please confirm or redirect before Phase 2):**
1. **First customer segment:** I recommend IT services and staffing firms doing talent rediscovery (India first). Alternatives: in-house corporate TA teams, or a candidate-facing product.
2. **Geography and data residency:** India first? EU or US later? This decides which compliance track applies first.
3. **Evaluation data:** can you get real CVs (consented or anonymized) plus recruiter judgements from a design partner? If not, we start with synthetic CVs plus volunteers and accept weaker validity.
4. **Budget and hardware:** is a GPU available? That decides between self-hosted and API embedding/rerank in the MVP.
5. **Team:** who builds this, and what do they already know? This affects the Celery vs Postgres-queue choice and LangGraph timing.

**Assumptions to validate in Phase 2:** OpenRouter batch support and current per-model prices; unified ATS API pricing; LinkedIn partner eligibility; whether Naukri RMS integration is possible; legal review of Resdex-sourced CVs; competitor positioning; the latency SLOs; whether ParadeDB `pg_search` is available on the chosen managed Postgres.

---

## 19. Next actions on approval (still no application code)

1. Create `D:\Learning\Project1\docs\01-phase1-strategic-analysis.md` containing this document.
2. Create `docs/adr/` with the initial ADRs:
   - 0001 modular monolith
   - 0002 Postgres + pgvector + BM25
   - 0003 Model Gateway with OpenRouter as one backend
   - 0004 bring-your-own-data, no scraping
   - 0005 MCP outbound only (Phase 8)
   - 0006 deterministic scoring, LLM verdicts only
   - 0007 relational capability graph instead of a graph DB
3. Create `docs/decision-log.md` and `docs/open-questions.md` (§18).
4. Save a project memory: the project's goals, the wedge, and the key decisions.
5. Then start Phase 2 planning (gold-set design, bake-off protocol) once you answer the §18 decisions.

**Verification:** the documents exist in `docs/` and render correctly; each ADR records the alternative considered and why it was rejected; you have confirmed or redirected the §18 decisions.

---

## Sources

- LinkedIn Talent: [How-To Guide](https://learn.microsoft.com/en-us/linkedin/talent) · [Apply Connect](https://learn.microsoft.com/en-us/linkedin/talent/apply-connect) · [RSC FAQ](https://learn.microsoft.com/en-us/linkedin/talent/recruiter-system-connect/rsc-faq) · [CRM Connect](https://learn.microsoft.com/en-us/linkedin/talent/crm-connect/crm-connect-overview) · [Partner program application](https://developer.linkedin.com/content/developer/global/en_us/index/partner-programs/apply) · [RSC security whitepaper](https://business.linkedin.com/content/dam/me/business/en-us/talent-solutions-lodestone/body/pdf/RSC_Security_Whitepaper_Booklet_Web.pdf)
- Naukri: [GetApp listing](https://www.getapp.com/hr-employee-management-software/a/naukri-rms) · [HackerEarth × Naukri RMS](https://www.hackerearth.com/blog/hackerearth-naukri-rms) · [Teamtailor Naukri integration](https://www.teamtailor.com/en/integrations/naukri-india)
- OpenRouter: [FAQ](https://openrouter.ai/docs/faq) · [Models](https://openrouter.ai/models) · [Fees analysis](https://www.activepieces.com/blog/understanding-openrouter-pricing-2026-and-fees.md) · [Capital & Compute review](https://capitalandcompute.net/blog/openrouter-ai/) · [Embedding models on OpenRouter](https://codewords.ai/blog/openrouter-embedding-models) · [Indicative prices (computeprices)](https://computeprices.com/providers/openrouter)
- Embeddings/rerankers: [PremAI embedding ranking](https://blog.premai.io/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting) · [Reranker comparison](https://www.beri.net/article/cohere-vs-voyage-vs-bge-reranker-per-search-vs-per-token-cost-2026) · [Reranker rankings](https://andrew.ooo/answers/best-reranker-models-2026-ranked/)
- Vector search: [Tiger Data pgvector vs Qdrant](https://www.tigerdata.com/blog/pgvector-vs-qdrant) · [Postgres vector options 2026](https://ecorpit.com/postgres-vector-search-pgvector-pgvectorscale-paradedb-lantern-2026/)
- MCP: [Authorization spec 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index) · [WorkOS on 2026 spec](https://workos.com/blog/mcp-2026-spec-agent-authentication)
- EU AI Act / Omnibus: [DLA Piper](https://knowledge.dlapiper.com/dlapiperknowledge/globalemploymentlatestdevelopments/2026/The-Digital-AI-Omnibus-Proposed-deferral-of-high-risk-AI-obligations-under-the-AI-Act) · [Ius Laboris](https://iuslaboris.com/insights/eu-ai-act-simplification-delays-employers/) · [Mondaq: Omnibus in force](https://www.mondaq.com/eu-digital-omnibus-on-ai-enters-into-force/1825722)
- India DPDP: [Hogan Lovells](https://www.hoganlovells.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-) · [AZB Partners](https://www.azbpartners.com/bank/update-indias-digital-personal-data-protection-framework-comes-into-effect/) · [YourStory](https://yourstory.com/2025/11/digital-personal-data-protection-dpdp-rules-2025-rollout-over-18-months)
- Claude model pricing: Anthropic list prices (cached 2026-10-06) from the Claude API reference.
