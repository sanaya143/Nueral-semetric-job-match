# ADR 0004: Bring-your-own-data ingestion; no scraping

- **Status:** Accepted
- **Date:** 2026-11-05
- **Phase:** 1

## Context
The original vision lists LinkedIn and Naukri as candidate sources. Research (Phase 1 analysis §5) found:
- **LinkedIn** exposes candidate data only through approved Talent Solutions partner programs:
  - **Apply Connect:** applicant data flows into a partner ATS.
  - **RSC:** limited fields are synced between an ATS and LinkedIn Recruiter.
  - **CRM Connect.**

  Scraping is prohibited by LinkedIn's User Agreement and has been litigated.
- **Naukri** has no public developer API that we could find. Naukri RMS (an ATS) and Resdex (a recruiter database) are per-seat products with licence terms.
- India's **DPDP** Rules phase in through about May 2027. The **EU AI Act** classes recruitment AI as high-risk (obligations from 2 Dec 2027).

## Decision
The platform works only on candidate data that the customer, or the candidate, is entitled to provide:
1. **MVP sources:**
   - Bulk CV upload (PDF/DOCX/ZIP).
   - CSV import.
   - Candidate-consented imports, e.g. a LinkedIn profile PDF the candidate uploads.
   - Our own apply flows.
2. **After the MVP:** ATS connectors, preferably through one unified ATS API vendor.
3. **Partnership track (in parallel):** a LinkedIn Talent Solutions partner application; an Info Edge (Naukri RMS) integration conversation.
4. **No scraping, and no third-party scraping APIs, under any circumstances.**

Every record stores its source, external ID, consent or lawful basis, and a retention TTL. The customer is the data controller/fiduciary; we act as processor under a DPA.

Each source plugs in through a connector interface: `fetch → map to canonical schema → emit change events`. Upserts are idempotent, keyed by `(tenant, source, external_id)`.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| Scrape LinkedIn/Naukri directly | Breaches platform terms, carries legal exposure, is unstable, and would disqualify us from partner programs |
| Third-party "LinkedIn/Naukri data APIs" | Usually scraping behind the scenes; same risks, plus supply-chain risk |
| Wait for partner approval before building | Blocks the product on an approval we don't control |

## Consequences
- The first market must already hold its own candidate data. This points to talent rediscovery for IT services and staffing firms.
- "Real-time interoperability" means event-driven ingestion from authorized systems, not crawling.
- Customer DPAs and our consent and retention tooling are MVP requirements.

## Revisit when
- A partner program is approved, or
- a platform publishes a new candidate-data API with acceptable terms.
