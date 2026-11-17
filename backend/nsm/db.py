"""Storage: SQLite (local, + FTS5 for BM25) or PostgreSQL (Azure, + built-in full-text search), and
tenant-scoped helpers.

The database is chosen by NSM_DATABASE_URL: empty means a local SQLite file; a postgresql+psycopg:// URL
means PostgreSQL (for example Azure Database for PostgreSQL Flexible Server, with sslmode=require).

Tenant isolation: every read goes through `scoped(session, Model, tenant_id)`, and the tenant itself now
comes from the caller's key or login token (nsm/security.py), never from a header the caller picks.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from nsm.config import get_settings
from nsm.models import AuditLog, Base

T = TypeVar("T")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        if settings.is_sqlite:
            _engine = create_engine(settings.db_url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        else:
            # pre_ping drops connections the server closed (Azure closes idle ones); recycle keeps them fresh.
            _engine = create_engine(settings.db_url, pool_pre_ping=True, pool_size=settings.db_pool_size,
                                    max_overflow=settings.db_pool_size, pool_recycle=1800)

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Used by tests and scripts after changing settings."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"


_PG_FTS = [
    "CREATE TABLE IF NOT EXISTS evidence_fts ("
    " unit_id VARCHAR(32) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL, candidate_id VARCHAR(32) NOT NULL,"
    " text TEXT NOT NULL, tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED)",
    "CREATE INDEX IF NOT EXISTS ix_evidence_fts_tsv ON evidence_fts USING GIN (tsv)",
    "CREATE INDEX IF NOT EXISTS ix_evidence_fts_tenant_cand ON evidence_fts (tenant_id, candidate_id)",
]


def init_db() -> None:
    engine = get_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            # Several workers may start at once: serialise schema creation with an advisory lock.
            conn.execute(text("SELECT pg_advisory_xact_lock(74210101)"))
            Base.metadata.create_all(conn)
            for stmt in _PG_FTS:
                conn.execute(text(stmt))
        return
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5("
                "text, unit_id UNINDEXED, tenant_id UNINDEXED, candidate_id UNINDEXED, "
                "tokenize='porter unicode61')"
            )
        )


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


def scoped(session: Session, model: type[T], tenant_id: str):  # noqa: ANN201
    return select(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]


def get_scoped(session: Session, model: type[T], tenant_id: str, obj_id: str) -> T | None:
    return session.scalars(scoped(session, model, tenant_id).where(model.id == obj_id)).first()  # type: ignore[attr-defined]


def audit(session: Session, tenant_id: str, action: str, entity: str, entity_id: str | None,
          detail: dict[str, Any] | None = None, actor: str = "system") -> None:
    session.add(AuditLog(tenant_id=tenant_id, action=action, entity=entity, entity_id=entity_id,
                         detail=detail or {}, actor=actor))


# ---------------------------------------------------------------------------------------
# Full-text index over evidence units: FTS5 BM25 on SQLite, tsvector + ts_rank_cd on PostgreSQL
# ---------------------------------------------------------------------------------------

def fts_index_units(session: Session, rows: list[tuple[str, str, str, str]]) -> None:
    """rows: (unit_id, tenant_id, candidate_id, text)"""
    for unit_id, tenant_id, candidate_id, body in rows:
        session.execute(
            text("INSERT INTO evidence_fts(text, unit_id, tenant_id, candidate_id) VALUES (:t, :u, :tn, :c)"),
            {"t": body, "u": unit_id, "tn": tenant_id, "c": candidate_id},
        )


def fts_delete_candidate(session: Session, tenant_id: str, candidate_id: str) -> None:
    session.execute(
        text("DELETE FROM evidence_fts WHERE candidate_id = :c AND tenant_id = :t"),
        {"c": candidate_id, "t": tenant_id},
    )


_FTS_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.]*")
_STOP = {"and", "or", "the", "with", "of", "in", "to", "a", "an", "for", "on", "at", "by", "is",
         "be", "as", "using", "use", "experience", "years", "year", "strong", "good", "knowledge"}


def fts_query_from_text(query: str, max_terms: int = 24) -> str:
    terms: list[str] = []
    for tok in _FTS_TOKEN.findall(query.lower()):
        tok = tok.strip(".")
        if len(tok) < 2 or tok in _STOP or tok in terms:
            continue
        terms.append(tok)
    return " OR ".join(f'"{t}"' for t in terms[:max_terms])


def fts_search(session: Session, tenant_id: str, query: str, limit: int = 200) -> list[tuple[str, str, float]]:
    """Returns [(unit_id, candidate_id, score)] where higher is better. Only the order is used (RRF)."""
    q = fts_query_from_text(query)
    if not q:
        return []
    if session.get_bind().dialect.name == "postgresql":
        # websearch_to_tsquery never raises on odd input (c++, node.js); "or" joins the terms.
        rows = session.execute(
            text(
                "SELECT unit_id, candidate_id, ts_rank_cd(tsv, q) AS s "
                "FROM evidence_fts, websearch_to_tsquery('english', :q) AS q "
                "WHERE tsv @@ q AND tenant_id = :t ORDER BY s DESC, unit_id LIMIT :n"
            ),
            {"q": q.replace(" OR ", " or "), "t": tenant_id, "n": limit},
        ).all()
        return [(r[0], r[1], float(r[2])) for r in rows]
    rows = session.execute(
        text(
            "SELECT unit_id, candidate_id, bm25(evidence_fts) AS s FROM evidence_fts "
            "WHERE evidence_fts MATCH :q AND tenant_id = :t ORDER BY s LIMIT :n"
        ),
        {"q": q, "t": tenant_id, "n": limit},
    ).all()
    return [(r[0], r[1], -float(r[2])) for r in rows]  # FTS5 bm25: lower = better
