"""Stage 1: per-atom hybrid retrieval over evidence units, fused with RRF.

Three ranked lists per requirement atom:
  dense    cosine over evidence-unit vectors (numpy now, pgvector later)
  lexical  BM25 over evidence units (SQLite FTS5 now, pg_search later)
  claims   candidates with capability claims for the atom's skills, by evidence level
Candidate score per atom = sum_src w_src / (k + rank_src); aggregated across atoms with
must 1.0 / learnable 0.5 / nice 0.5 weights and normalised to [0, 1]. $0 per run.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from nsm.db import fts_search
from nsm.gateway import Gateway
from nsm.models import Candidate, SkillClaim
from nsm.retrieval import vector_index
from nsm.scoring.types import LEVEL_SCORE, AtomSpec
from nsm.taxonomy import get_taxonomy

RRF_K = 60
SRC_W = {"dense": 1.0, "lexical": 1.0, "claims": 1.5}
ATOM_W = {"must": 1.0, "learnable": 0.5, "nice": 0.5}
PER_ATOM_UNITS = 400


@dataclass
class Stage1:
    ranked: list[tuple[str, float]]  # (candidate_id, score in [0,1]) best first
    semantic_hits: dict[str, dict[str, tuple[str, float]]] = field(default_factory=dict)  # cid -> atom -> (unit, sim)
    stats: dict = field(default_factory=dict)


def atom_query(a: AtomSpec) -> str:
    tax = get_taxonomy()
    return " ".join([a.text, *(tax.query_terms(s) for s in a.skill_ids)])


def _ranks(ordered_cids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for cid in ordered_cids:
        if cid not in out:
            out[cid] = len(out) + 1
    return out


def retrieve(session: Session, gateway: Gateway, tenant_id: str, atoms: list[AtomSpec], allowed: set[str],
             top_k: int, run_id: str | None = None) -> Stage1:
    q_atoms = [a for a in atoms if a.retrievable]
    if not q_atoms or not allowed:
        return Stage1(ranked=[(cid, 0.0) for cid in list(allowed)[:top_k]], stats={"atoms_retrieved": 0})

    # dense: one embedding call for all atom queries, per embedding model present in the index
    dense: dict[str, list[tuple[str, str, float]]] = {a.id: [] for a in q_atoms}
    for model in vector_index.models_in_use(session, tenant_id):
        vecs, used = gateway.embed(session, [atom_query(a) for a in q_atoms], tenant_id=tenant_id, model=model,
                                   run_id=run_id)
        if used != model:
            continue  # query could not be embedded with this model (e.g. offline): skip, other sources still work
        for a, hits in zip(q_atoms, vector_index.search(session, tenant_id, model, vecs, PER_ATOM_UNITS, allowed)):
            dense[a.id].extend(hits)
    for aid in dense:
        dense[aid].sort(key=lambda h: -h[2])

    # claims: one query for all skills
    all_skills = sorted({s for a in q_atoms for s in a.skill_ids})
    claim_rows: dict[str, dict[str, float]] = defaultdict(dict)  # skill -> cid -> best strength
    if all_skills:
        rows = session.execute(
            select(SkillClaim.skill_id, SkillClaim.candidate_id, SkillClaim.level, SkillClaim.confidence)
            .join(Candidate, Candidate.id == SkillClaim.candidate_id)
            .where(SkillClaim.tenant_id == tenant_id, SkillClaim.skill_id.in_(all_skills), Candidate.status == "ready")
        ).all()
        for sid, cid, level, conf in rows:
            if cid not in allowed:
                continue
            strength = LEVEL_SCORE.get(level, 0.0) * (0.5 + 0.5 * (conf or 0.5))
            if strength > claim_rows[sid].get(cid, 0.0):
                claim_rows[sid][cid] = strength

    total: dict[str, float] = defaultdict(float)
    semantic: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    total_w = 0.0
    max_atom = sum(SRC_W.values()) / (RRF_K + 1)
    for a in q_atoms:
        aw = ATOM_W.get(a.kind, 0.5) * a.weight
        total_w += aw
        d_hits = dense[a.id]
        for uid, cid, sim in d_hits:  # best unit per candidate for this atom
            if a.id not in semantic[cid]:
                semantic[cid][a.id] = (uid, sim)
        lists = {"dense": _ranks([h[1] for h in d_hits])}
        lex = [(u, c, s) for u, c, s in fts_search(session, tenant_id, atom_query(a), PER_ATOM_UNITS) if c in allowed]
        lists["lexical"] = _ranks([h[1] for h in lex])
        if a.skill_ids:
            strength: dict[str, float] = defaultdict(float)
            for sid in a.skill_ids:
                for cid, s in claim_rows.get(sid, {}).items():
                    strength[cid] = max(strength[cid], s) if a.logic == "any" else strength[cid] + s / len(a.skill_ids)
            lists["claims"] = _ranks([c for c, _ in sorted(strength.items(), key=lambda kv: -kv[1])])
        per_atom: dict[str, float] = defaultdict(float)
        for src, ranks in lists.items():
            for cid, r in ranks.items():
                per_atom[cid] += SRC_W[src] / (RRF_K + r)
        for cid, s in per_atom.items():
            total[cid] += aw * s / max_atom

    ranked = sorted(((cid, round(s / total_w, 4)) for cid, s in total.items() if cid in allowed),
                    key=lambda kv: -kv[1])[:top_k]
    return Stage1(ranked=ranked, semantic_hits=dict(semantic),
                  stats={"atoms_retrieved": len(q_atoms), "candidates_scored": len(total), "kept": len(ranked)})
