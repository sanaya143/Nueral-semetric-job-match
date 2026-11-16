"""Job intake with human-in-the-loop confirmation.

draft (parsed atoms) -> recruiter edits atoms -> confirmed -> match runs.
Editing a confirmed job's atoms bumps `version`, so old match runs stay reproducible
(they record job_version) and are shown as stale.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from nsm.db import audit
from nsm.gateway import Gateway
from nsm.jobs.requirements import AtomDraft, JdDraft, parse_jd_heuristic, parse_jd_llm
from nsm.models import Job, RequirementAtom
from nsm.taxonomy import get_taxonomy

KINDS = {"must", "nice", "learnable"}
DIMENSIONS = {"technical", "domain", "experience", "logistics", "education"}


def create_job(session: Session, gateway: Gateway, tenant_id: str, title: str, description: str,
               use_llm: bool = True) -> tuple[Job, JdDraft]:
    draft = (parse_jd_llm(session, gateway, tenant_id, title, description) if use_llm and gateway.llm_enabled
             else parse_jd_heuristic(title, description))
    job = Job(tenant_id=tenant_id, title=title[:250], description=description, status="draft",
              seniority_years=draft.seniority_years, location=draft.location, work_mode=draft.work_mode,
              extraction_mode=draft.mode)
    session.add(job)
    session.flush()
    _replace_atoms(session, job, draft.atoms)
    audit(session, tenant_id, "job.created", "job", job.id, {"atoms": len(draft.atoms), "mode": draft.mode})
    return job, draft


def _replace_atoms(session: Session, job: Job, atoms: list[AtomDraft]) -> None:
    tax = get_taxonomy()
    job.atoms.clear()
    session.flush()
    for i, a in enumerate(atoms):
        skills = [s for s in a.skill_ids if s in tax.skills]
        job.atoms.append(RequirementAtom(
            tenant_id=job.tenant_id, position=i, text=a.text[:500],
            kind=a.kind if a.kind in KINDS else "must",
            dimension=a.dimension if a.dimension in DIMENSIONS else "technical",
            skill_ids=skills, logic=a.logic if a.logic in ("all", "any") else "all",
            min_years=a.min_years, weight=max(0.1, min(3.0, float(a.weight or 1.0)))))
    session.flush()


def update_atoms(session: Session, job: Job, atoms: list[AtomDraft], *, seniority_years: float | None = None,
                 location: str | None = None, work_mode: str | None = None, actor: str = "recruiter") -> Job:
    _replace_atoms(session, job, atoms)
    job.seniority_years, job.location, job.work_mode = seniority_years, location, work_mode
    if job.status == "confirmed":
        job.version += 1
        job.status = "draft"
    audit(session, job.tenant_id, "job.atoms_edited", "job", job.id, {"atoms": len(atoms)}, actor=actor)
    return job


def confirm_job(session: Session, job: Job, actor: str = "recruiter") -> Job:
    if not job.atoms:
        raise ValueError("a job needs at least one requirement before it can be confirmed")
    job.status = "confirmed"
    audit(session, job.tenant_id, "job.confirmed", "job", job.id, {"version": job.version}, actor=actor)
    return job


def atoms_from_payload(items: list[dict]) -> list[AtomDraft]:
    tax = get_taxonomy()
    out = []
    for it in items:
        text = str(it.get("text", "")).strip()
        if not text:
            continue
        skills = it.get("skill_ids")
        if skills is None:
            skills = tax.skill_ids_in(text)
        out.append(AtomDraft(text=text, kind=it.get("kind", "must"), dimension=it.get("dimension", "technical"),
                             skill_ids=list(skills), logic=it.get("logic", "all"), min_years=it.get("min_years"),
                             weight=it.get("weight", 1.0)))
    return out
