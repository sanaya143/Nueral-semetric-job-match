"""Deterministic scorer (ADR 0006): verdicts in, ranked and explained scores out.

The LLM never produces the number. Same verdicts -> same score, every time.

Dimensions (defaults from the Phase 1 analysis, renormalised over the dimensions a job uses):
  technical 0.30 · practical 0.25 · domain 0.15 · nice 0.10 · seniority 0.10 · logistics 0.10
  education 0.00 (fairness default; a must-have degree is enforced by the gate instead)
Gate: every must-have technical/domain/education atom must be met (>= applied);
experience and logistics musts fail the gate only when clearly not met (unknown never fails).
"""

from __future__ import annotations

from datetime import date

from nsm.scoring.types import STATUS_SCORE, CandidateView, JobSpec

WEIGHTS = {"technical": 0.30, "practical": 0.25, "domain": 0.15, "nice": 0.10, "seniority": 0.10,
           "logistics": 0.10, "education": 0.0}
KIND_WEIGHT = {"must": 1.0, "learnable": 0.5, "nice": 1.0}
SCORER_VERSION = "scorer-v1"

FLAG_TEXT = {
    "hidden_text_removed": "Hidden text (e.g. white or tiny font) was removed from the document",
    "possible_prompt_injection": "Document contained text addressed to AI screening tools; it was ignored",
    "skills_list_without_evidence": "Long skills list with little described work behind it",
    "senior_title_low_tenure": "Senior title with short total tenure; verify scope",
    "overlapping_roles": "Overlapping role dates; verify timeline",
    "unstructured_cv": "CV has no recognisable sections; extraction confidence is lower",
}


def _working_years_since(cv: CandidateView, unit) -> float | None:  # noqa: ANN001
    """Years of *work* (dated roles) after this evidence ended. Career breaks do not count, so
    a gap never lowers recency (fairness: gaps are not a scoring input; checked by the
    counterfactual test in scripts/evaluate.py)."""
    if unit.kind != "experience" or unit.start_year is None:
        return None
    now = date.today().year + date.today().month / 12
    u_end = unit.end_year or now
    spans = sorted((max(u.start_year, u_end), u.end_year or now) for u in cv.units.values()
                   if u.kind == "experience" and u.start_year and (u.end_year or now) > u_end)
    total, last = 0.0, None
    for s, e in spans:
        if last is not None and s < last:
            s = last
        if e > s:
            total += e - s
            last = e if last is None else max(last, e)
    return total


def _recency(cv: CandidateView, unit) -> float:  # noqa: ANN001
    since = _working_years_since(cv, unit)
    if since is None:
        return 0.9  # undated projects / achievements
    return 1.0 if since <= 3 else 0.85 if since <= 6 else 0.7


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    tw = sum(w for _, w in pairs)
    return sum(s * w for s, w in pairs) / tw if tw > 0 else None


def score_candidate(job: JobSpec, cv: CandidateView, verdicts: list[dict]) -> dict:
    atoms = {a.id: a for a in job.atoms}
    dims: dict[str, list[tuple[float, float]]] = {k: [] for k in WEIGHTS}
    practical: list[tuple[float, float]] = []
    gate_failures: list[str] = []

    for v in verdicts:
        a = atoms[v["atom_id"]]
        w = a.weight * KIND_WEIGHT.get(a.kind, 1.0)
        status = v["status"]
        if a.kind == "must":
            if a.dimension in ("technical", "domain", "education") and status != "met":
                gate_failures.append(a.text)
            elif a.dimension in ("experience", "logistics") and status == "not_met":
                gate_failures.append(a.text)
        if a.dimension == "logistics":
            dims["logistics"].append((STATUS_SCORE[status], w))
            continue
        if a.dimension == "experience" and a.min_years:
            dims["seniority"].append((v["score"], w))
            continue
        if a.kind == "nice":
            dims["nice"].append((v["score"], w))
            continue
        dim = a.dimension if a.dimension in ("domain", "education") else "technical"
        dims[dim].append((v["score"], w))
        if dim == "technical":  # practical depth/recency of the evidence for technical requirements
            p = 0.0
            ev = v["evidence"][0] if v.get("evidence") else None
            unit = cv.units.get(ev["unit_id"]) if ev else None
            if v["level"] == "demonstrated" and unit is not None:
                p = (1.0 if unit.kind == "experience" else 0.8) * _recency(cv, unit)
            elif v["level"] == "applied" and unit is not None:
                p = 0.5 * _recency(cv, unit)
            elif v["level"] == "inferred":
                p = 0.2
            practical.append((p, w))
    dims["practical"] = practical
    if job.seniority_years and not dims["seniority"]:
        dims["seniority"].append((min(1.0, cv.total_years / job.seniority_years), 1.0))

    dim_scores: dict[str, float] = {}
    total_w = acc = 0.0
    for d, pairs in dims.items():
        s = _wmean(pairs)
        if s is None:
            continue
        dim_scores[d] = round(s * 100, 1)
        acc += WEIGHTS[d] * s
        total_w += WEIGHTS[d]
    score = round(100 * acc / total_w, 1) if total_w > 0 else 0.0
    gate_passed = not gate_failures

    # confidence: evidence specificity, verification, unknowns, document quality
    confs = [v.get("confidence", 0.5) for v in verdicts if v["dimension"] != "logistics"]
    confidence = sum(confs) / len(confs) if confs else 0.5
    if any(v.get("verified_by") == "llm" for v in verdicts):
        confidence = min(1.0, confidence + 0.05)
    unknown = sum(1 for v in verdicts if v["status"] == "unknown")
    confidence -= 0.03 * unknown
    if "unstructured_cv" in cv.flags:
        confidence -= 0.1
    confidence = round(max(0.05, min(1.0, confidence)), 2)
    band = "high" if confidence >= 0.75 else "medium" if confidence >= 0.5 else "low"

    if gate_passed and score >= 75:
        suitability = "strong_match"
    elif gate_passed and score >= 55:
        suitability = "good_match"
    elif score >= 40:
        suitability = "partial_match"
    else:
        suitability = "low_match"

    concerns: list[dict] = []
    for f in gate_failures:
        concerns.append({"type": "must_have_gap", "text": f"Must-have not evidenced: {f}"})
    for v in verdicts:
        if v["status"] == "unknown":
            concerns.append({"type": "unknown", "text": f"Not stated in CV: {v['atom_text']} ({v['note']})"})
        elif v["level"] == "inferred" and v["kind"] == "must":
            concerns.append({"type": "inferred", "text": f"Only inferred, verify in interview: {v['atom_text']}"})
    for f in cv.flags:
        key = f.split(":")[0]
        if key in FLAG_TEXT:
            concerns.append({"type": "needs_verification", "text": FLAG_TEXT[key]})
        elif key == "unsupported_expertise_claims":
            concerns.append({"type": "needs_verification",
                             "text": "Claims expertise without supporting described work: " + f.split(":", 1)[1]})
    return {"score": score, "gate_passed": gate_passed, "gate_failures": gate_failures, "dimension_scores": dim_scores,
            "confidence": confidence, "confidence_band": band, "suitability": suitability, "concerns": concerns}


def sort_key(r: dict) -> tuple:
    return (not r["gate_passed"], -r["score"], -r["confidence"])
