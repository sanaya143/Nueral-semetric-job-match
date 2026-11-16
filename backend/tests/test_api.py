"""End-to-end through the HTTP API (FastAPI TestClient; background tasks run inline)."""

from __future__ import annotations

from conftest import CV_A, CV_B, JD_AI
from fastapi.testclient import TestClient

from nsm.api.app import app

H = {"X-Tenant-ID": "t-api"}


def test_full_recruiter_flow():
    with TestClient(app) as c:
        assert c.get("/api/v1/health").json()["status"] == "ok"
        up = c.post("/api/v1/candidates/upload", headers=H, files=[
            ("files", ("a.txt", CV_A.encode(), "text/plain")),
            ("files", ("b.txt", CV_B.encode(), "text/plain"))]).json()["uploaded"]
        assert len(up) == 2
        cands = c.get("/api/v1/candidates", headers=H).json()
        assert cands["total"] == 2 and all(x["status"] == "ready" for x in cands["items"])
        b_id = next(u["id"] for u in up if u["filename"] == "b.txt")
        prof = c.get(f"/api/v1/candidates/{b_id}", headers=H).json()
        assert prof["contact"]["full_name"] == "Kavya Iyer"
        assert any(cap["skill_id"] == "rag" and cap["level"] == "demonstrated" for cap in prof["capabilities"])

        job = c.post("/api/v1/jobs", headers=H, json={"title": "AI Engineer", "description": JD_AI}).json()
        assert job["status"] == "draft"
        assert c.post(f"/api/v1/jobs/{job['id']}/match", headers=H, json={}).status_code == 400  # HITL first
        atoms = job["atoms"]
        atoms[-1]["kind"] = "nice"
        job = c.put(f"/api/v1/jobs/{job['id']}/requirements", headers=H,
                    json={"atoms": atoms, "seniority_years": 3, "location": "Bengaluru", "work_mode": "hybrid"}).json()
        job = c.post(f"/api/v1/jobs/{job['id']}/confirm", headers=H).json()
        assert job["status"] == "confirmed"

        run = c.post(f"/api/v1/jobs/{job['id']}/match", headers=H, json={"verify": True}).json()
        res = c.get(f"/api/v1/runs/{run['id']}", headers=H).json()
        assert res["status"] == "done" and res["cost_usd"] == 0
        top = res["results"][0]
        assert top["candidate"]["id"] == b_id and top["candidate"]["display_name"].startswith("Candidate ")  # blind

        rep = c.get(f"/api/v1/runs/{run['id']}/candidates/{b_id}", headers=H).json()
        assert rep["verdicts"] and rep["deep_eval"]["summary"] and rep["provenance"]["scorer"]
        de = c.post(f"/api/v1/runs/{run['id']}/candidates/{b_id}/deep-eval", headers=H).json()
        assert de["mode"] == "offline"

        ids = ",".join(r["candidate"]["id"] for r in res["results"][:2])
        cmp = c.get(f"/api/v1/runs/{run['id']}/compare", headers=H, params={"ids": ids}).json()
        assert len(cmp["candidates"]) == 2 and cmp["differentiators"]

        found = c.post("/api/v1/search", headers=H, json={"query": "built RAG with pgvector in python"}).json()
        assert found["results"][0]["candidate"]["id"] == b_id

        assert c.post("/api/v1/feedback", headers=H, json={"run_id": run["id"], "candidate_id": b_id,
                                                           "action": "shortlist", "reason_code": "strong_evidence"}
                      ).json()["ok"]
        assert c.get("/api/v1/admin/costs", headers=H).json()["caps"]["per_run_usd"] == 0.20
        actions = {a["action"] for a in c.get("/api/v1/admin/audit", headers=H).json()["items"]}
        assert {"candidate.uploaded", "job.confirmed", "run.finished", "feedback"} <= actions

        # tenant isolation over HTTP
        assert c.get(f"/api/v1/candidates/{b_id}", headers={"X-Tenant-ID": "someone-else"}).status_code == 404
        assert c.get(f"/api/v1/runs/{run['id']}", headers={"X-Tenant-ID": "someone-else"}).status_code == 404

        # erasure
        assert c.delete(f"/api/v1/candidates/{b_id}", headers=H).json()["verified"] is True
        assert c.get(f"/api/v1/candidates/{b_id}", headers=H).status_code == 404


def test_upload_rejects_unsupported_types():
    with TestClient(app) as c:
        r = c.post("/api/v1/candidates/upload", headers=H, files=[("files", ("x.exe", b"MZ", "application/x"))])
        assert r.status_code == 415


def test_invalid_tenant_header():
    with TestClient(app) as c:
        assert c.get("/api/v1/candidates", headers={"X-Tenant-ID": "../etc"}).status_code == 400
