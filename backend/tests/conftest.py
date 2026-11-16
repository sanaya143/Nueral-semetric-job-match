"""Test setup: isolated temp storage, offline mode, and a fake OpenRouter client.

Tests never call a real model or spend money. The fake client returns canned JSON and
usage so the gateway's budget guards, cost meter and citation validation can be tested.
"""

from __future__ import annotations

import os

import pytest

os.environ["NSM_LLM_MODE"] = "offline"
os.environ["NSM_EMBEDDING_MODE"] = "local"
os.environ["NSM_OPENROUTER_API_KEY"] = ""
os.environ["NSM_RERANK_MODE"] = "none"
os.environ["NSM_ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"
os.environ["NSM_MODEL_SMALL"] = "test/small-model"
os.environ["NSM_MODEL_LARGE"] = "test/large-model"
os.environ["NSM_AUTH_MODE"] = "none"
# Limits are switched off for the suite (it makes many requests quickly); test_production.py turns them on.
os.environ["NSM_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["NSM_WRITE_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["NSM_DAILY_UPLOAD_QUOTA"] = "0"
os.environ["NSM_DAILY_MATCH_RUN_QUOTA"] = "0"
os.environ["NSM_MODEL_PRICES"] = '{"test/small-model": [0.10, 0.50, 0.01], "test/large-model": [2.0, 10.0, 0.20]}'

from nsm.config import reset_settings_cache  # noqa: E402
from nsm.db import init_db, reset_engine  # noqa: E402
from nsm.gateway import reset_gateway  # noqa: E402
from nsm.gateway.clients import Usage  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_storage(tmp_path_factory):
    d = tmp_path_factory.mktemp("nsm-data")
    os.environ["NSM_DATA_DIR"] = str(d)
    # Run the same suite on PostgreSQL by setting NSM_TEST_DATABASE_URL to an EMPTY test database.
    pg = os.environ.get("NSM_TEST_DATABASE_URL", "")
    if pg:
        from sqlalchemy import create_engine, text

        eng = create_engine(pg)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        eng.dispose()
    os.environ["NSM_DATABASE_URL"] = pg
    reset_settings_cache()
    reset_engine()
    reset_gateway()
    init_db()
    yield d
    reset_engine()


CV_A = """Rahul Verma
AI Engineer | 4 years | Pune
rahul.v@example.com | +91 98765 43210

PROFESSIONAL SUMMARY
Passionate AI Engineer with expertise in Artificial Intelligence, Machine Learning, LLMs, RAG and Vector Databases.

EXPERIENCE
AI Engineer | Quantik Solutions, Pune | Jan 2022 - Present
- Worked on various AI initiatives across the organisation
- Responsible for supporting the team on machine learning tasks
- Exposure to cloud technologies and DevOps practices

SKILLS
Python, Artificial Intelligence, Machine Learning, Deep Learning, NLP, LLM, RAG, Vector Databases, MLOps, AWS, Docker, Kubernetes, LangChain

PERSONAL DETAILS
Date of Birth: 12/03/1996
Religion: Hindu
Marital Status: Single
"""

CV_B = """Kavya Iyer
Backend Engineer | Bengaluru

Work Experience
Software Engineer, Meridian Payments (06/2021 - Present)
- Built an internal document Q&A assistant using the OpenAI API, serving 300 employees
- Implemented a document retrieval pipeline with chunking and pgvector, feeding context to the LLM
- Deployed the assistant to production on AWS ECS using Docker
- Developed Python backend services with FastAPI
Tech stack: Python, FastAPI, PostgreSQL, Redis

Software Engineer
Indigo Lattice Software
Jul 2019 to May 2021
- Designed REST APIs for a payments reconciliation platform

Education
B.Tech, Computer Science - State Institute of Technology (2019)

Preferences
Preferred location: Bengaluru | Work mode: Hybrid | Notice period: 30 days
"""

JD_AI = """About the role
Internal knowledge assistant for an operations team.

Must have
- Strong Python
- Hands-on experience building LLM applications (OpenAI API, Claude API or open models)
- Built RAG pipelines: chunking, embeddings, retrieval and grounding
- Experience with a vector database (pgvector, Qdrant, Pinecone or similar)
- Production deployment: you have shipped services into production
- 3+ years of software engineering experience

Nice to have
- LangChain or LangGraph
- Docker and AWS

Logistics
- Location: Bengaluru (Hybrid)
- Notice period: 30 days or less
"""


class FakeClient:
    """Stands in for OpenRouterClient. `responses` is a list of dicts (or callables taking the
    user prompt) returned in order; every call reports fixed usage."""

    def __init__(self, responses, input_tokens=2000, output_tokens=400, cost=None):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.input_tokens, self.output_tokens, self.cost = input_tokens, output_tokens, cost

    def chat_json(self, model, system, user, schema, schema_name, max_tokens, effort):
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema_name,
                           "max_tokens": max_tokens, "effort": effort})
        r = self.responses.pop(0) if self.responses else {}
        if isinstance(r, Exception):
            raise r
        data = r(user) if callable(r) else r
        return data, Usage(input_tokens=self.input_tokens, output_tokens=self.output_tokens, cost_usd=self.cost,
                           latency_ms=5)

    def embed(self, model, texts):
        raise AssertionError("tests use local embeddings")


@pytest.fixture
def llm_gateway():
    """A Gateway in openrouter mode whose HTTP client is replaced by a FakeClient."""
    from nsm.config import Settings
    from nsm.gateway import Gateway

    def make(responses, **kw):
        s = Settings(llm_mode="openrouter", openrouter_api_key="test-key-not-real", embedding_mode="local",
                     **{k: v for k, v in kw.items() if k.startswith("cap_")})
        gw = Gateway(s)
        gw._client = FakeClient(responses, **{k: v for k, v in kw.items() if not k.startswith("cap_")})
        return gw
    return make
