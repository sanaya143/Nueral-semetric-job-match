"""Load the synthetic dataset into local storage and run one match per job.

    backend/.venv/Scripts/python.exe scripts/seed_demo.py --reset

--reset deletes data/nsm.db and data/uploads first (local demo data only).
Runs in whatever mode .env selects (offline by default: $0).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from nsm.config import get_settings
from nsm.db import get_scoped, init_db, reset_engine, session_scope
from nsm.gateway import get_gateway
from nsm.ingestion.pipeline import ingest_bytes
from nsm.jobs.service import confirm_job, create_job
from nsm.matching.service import run_match_sync
from nsm.models import Job, MatchRun

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "synthetic"


def reset_storage() -> None:
    s = get_settings()
    reset_engine()
    for p in (s.data_dir / "nsm.db", s.data_dir / "nsm.db-wal", s.data_dir / "nsm.db-shm"):
        p.unlink(missing_ok=True)
    if s.uploads_dir.exists():
        shutil.rmtree(s.uploads_dir)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe local demo storage first")
    ap.add_argument("--tenant", default=get_settings().default_tenant)
    ap.add_argument("--no-match", action="store_true")
    args = ap.parse_args()
    if not (DATA / "cvs").exists():
        print("datasets/synthetic not found: run scripts/generate_synthetic.py first", file=sys.stderr)
        return 1
    if args.reset:
        reset_storage()
    init_db()
    gw = get_gateway()
    print(f"mode: {'openrouter' if gw.llm_enabled else 'offline'} | embeddings: {gw.embedding_model}")

    t0 = time.perf_counter()
    statuses: dict[str, int] = {}
    for f in sorted((DATA / "cvs").iterdir()):
        _cid, status = ingest_bytes(args.tenant, f.name, f.read_bytes(), source="synthetic", external_id=f.stem,
                                    bulk=True)
        statuses[status] = statuses.get(status, 0) + 1
    print(f"ingested {sum(statuses.values())} CVs in {time.perf_counter() - t0:.1f}s: {statuses}")

    job_ids = []
    for f in sorted((DATA / "jds").glob("*.txt")):
        title, _, body = f.read_text(encoding="utf-8").partition("\n")
        with session_scope() as s:
            job, draft = create_job(s, gw, args.tenant, title.strip(), body.strip())
            confirm_job(s, job)
            job_ids.append(job.id)
            print(f"job {title.strip()!r}: {len(draft.atoms)} atoms "
                  f"({sum(1 for a in draft.atoms if a.kind == 'must')} must) {draft.notes or ''}")
    if args.no_match:
        return 0
    for jid in job_ids:
        t1 = time.perf_counter()
        rid = run_match_sync(args.tenant, jid)
        with session_scope() as s:
            run = get_scoped(s, MatchRun, args.tenant, rid)
            job = get_scoped(s, Job, args.tenant, jid)
            print(f"run {job.title!r}: {run.status} in {time.perf_counter() - t1:.1f}s, "
                  f"{run.stats.get('results', 0)} results, ${run.cost_usd:.4f} {run.error or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
