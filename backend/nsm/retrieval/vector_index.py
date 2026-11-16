"""Brute-force numpy vector index over evidence units (initial local build).

Exact cosine search is fine to ~100K units on a laptop (100K x 512 float32 = 200 MB worst
case; typical local corpora are far smaller). The production build swaps this for
pgvector HNSW behind the same `search()` signature (ADR 0002 / ADR 0009).
Cached per (tenant, embedding model); invalidated on ingest and erasure.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from nsm.models import Candidate, EvidenceUnit

_lock = threading.Lock()
_cache: dict[tuple[str, str], "_Index"] = {}
_versions: dict[str, int] = {}


@dataclass
class _Index:
    version: int
    unit_ids: list[str]
    candidate_ids: list[str]
    matrix: np.ndarray  # (n, d), L2-normalised


def invalidate(tenant_id: str) -> None:
    with _lock:
        _versions[tenant_id] = _versions.get(tenant_id, 0) + 1


def models_in_use(session: Session, tenant_id: str) -> list[str]:
    rows = session.execute(
        select(EvidenceUnit.embedding_model).join(Candidate, Candidate.id == EvidenceUnit.candidate_id)
        .where(EvidenceUnit.tenant_id == tenant_id, Candidate.status == "ready",
               EvidenceUnit.embedding_model.is_not(None)).distinct()
    ).all()
    return [r[0] for r in rows]


def _load(session: Session, tenant_id: str, model: str) -> _Index:
    version = _versions.get(tenant_id, 0)
    key = (tenant_id, model)
    idx = _cache.get(key)
    if idx is not None and idx.version == version:
        return idx
    rows = session.execute(
        select(EvidenceUnit.id, EvidenceUnit.candidate_id, EvidenceUnit.embedding)
        .join(Candidate, Candidate.id == EvidenceUnit.candidate_id)
        .where(EvidenceUnit.tenant_id == tenant_id, EvidenceUnit.embedding_model == model,
               Candidate.status == "ready", EvidenceUnit.embedding.is_not(None))
    ).all()
    if rows:
        mat = np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
    else:
        mat = np.zeros((0, 1), dtype=np.float32)
    idx = _Index(version, [r[0] for r in rows], [r[1] for r in rows], mat)
    with _lock:
        _cache[key] = idx
    return idx


def search(session: Session, tenant_id: str, model: str, query_vecs: np.ndarray, top_n: int,
           allowed_candidates: set[str] | None = None) -> list[list[tuple[str, str, float]]]:
    """For each query vector: [(unit_id, candidate_id, cosine)] best first."""
    idx = _load(session, tenant_id, model)
    if idx.matrix.shape[0] == 0:
        return [[] for _ in range(len(query_vecs))]
    sims = query_vecs @ idx.matrix.T  # (q, n)
    if allowed_candidates is not None:
        mask = np.array([c in allowed_candidates for c in idx.candidate_ids])
        sims = np.where(mask[None, :], sims, -np.inf)
    out: list[list[tuple[str, str, float]]] = []
    n = min(top_n, sims.shape[1])
    for row in sims:
        top = np.argpartition(-row, n - 1)[:n]
        top = top[np.argsort(-row[top])]
        out.append([(idx.unit_ids[i], idx.candidate_ids[i], float(row[i])) for i in top if np.isfinite(row[i])])
    return out
