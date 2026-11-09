# ADR 0006: Deterministic scoring; LLMs produce cited verdicts, not scores

- **Status:** Accepted
- **Date:** 2026-11-05
- **Phase:** 1

## Context
The product must rank candidates on demonstrated capability, explain why, never fabricate experience, and be auditable. That last point matters because recruitment AI is high-risk under the EU AI Act and bias audits may be required. LLM-generated scores are non-reproducible, hard to calibrate, and open to prompt injection hidden in CVs.

## Decision
1. **Verdicts, not scores.** For each requirement atom, the LLM returns:
   - an **evidence-level verdict** (`demonstrated | applied | inferred | listed_only | missing`)
   - a short rationale
   - **cited evidence-unit IDs** and exact quotes
2. **Citation validator.** A deterministic validator rejects any citation that:
   - points to an evidence unit the candidate doesn't have, or
   - quotes text that isn't an exact substring of the source.
3. **Deterministic scorer.** The final score is a deterministic function of the validated verdicts. The scorer applies:
   - must-have gates
   - weighted dimensions, configurable per job (defaults in analysis §11)
   - recency and specificity adjustments
   - a confidence formula
4. **Reproducibility.** Each result stores the job version, candidate version, model ID and prompt version.
5. **Labelling.** Inferred capabilities are always labelled as inferred, kept visually distinct, and weighted low.
6. **Recruiter decides.** The system never issues a hire/reject decision.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| LLM outputs a 0–100 match score | Not reproducible, poorly calibrated, open to injection, hard to audit |
| Pure embedding cosine similarity | Rewards keyword/semantic overlap rather than demonstrated work, and can't tell "listed" from "did" |
| Learned ranking model from day one | No labelled data yet; planned as Phase 8 learning-to-rank once feedback accumulates |

## Consequences
- Rankings are explainable and repeatable, and can be audited for fairness. Counterfactual invariance tests become possible.
- Weights need calibration against the gold evaluation set (Phase 2/4).
- There are more moving parts (validator, scorer) than in a single LLM call.

## Revisit when
- Enough validated recruiter feedback exists to train a learning-to-rank layer on top of the verdict features.
