"""Production hardening (version 1.2):

- OCR: scanned PDFs and image CVs are read (local engine), Azure Document Intelligence client, OCR off.
- Workspace security: the workspace comes from an API key or an Entra ID token, never from a header the
  caller picks; a mismatching X-Tenant-ID header is refused.
- Rate limits and daily quotas.
- Readiness check.
"""

from __future__ import annotations

import hashlib
import json
import time

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from nsm.api.app import app
from nsm.config import reset_settings_cache
from nsm.security import limiter

CV_LINES = ["Priya Sharma", "Experience", "Data Engineer, Northwind Analytics (2019 - present)",
            "- Built data pipelines in Python and Spark for the finance team.",
            "- Deployed services to production on AWS using Docker."]


@pytest.fixture
def env(monkeypatch):
    """Set NSM_* settings for one test; everything is restored afterwards."""
    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(f"NSM_{k.upper()}", str(v))
        reset_settings_cache()
    yield set_
    monkeypatch.undo()
    reset_settings_cache()
    limiter.reset()


def _page_png() -> bytes:
    doc = pymupdf.open()
    pg = doc.new_page()
    for i, line in enumerate(CV_LINES):
        pg.insert_text((72, 72 + 22 * i), line, fontsize=12)
    return pg.get_pixmap(dpi=200).tobytes("png")


def _scanned_pdf() -> bytes:
    scan = pymupdf.open()
    page = scan.new_page()
    page.insert_image(page.rect, stream=_page_png())
    return scan.tobytes(garbage=3, deflate=True)


# --- OCR -------------------------------------------------------------------------------

def test_scanned_pdf_is_read_with_ocr():
    pytest.importorskip("rapidocr_onnxruntime")
    from nsm.ingestion.parsing import parse_pdf

    p = parse_pdf(_scanned_pdf())
    assert p.ocr_pages == 1
    assert "Northwind Analytics" in p.text and "data pipelines in Python and Spark" in p.text


def test_scanned_pdf_and_image_upload_become_ready_candidates():
    pytest.importorskip("rapidocr_onnxruntime")
    h = {"X-Tenant-ID": "t-ocr"}
    with TestClient(app) as c:
        r = c.post("/api/v1/candidates/upload", headers=h,
                   files=[("files", ("scan.pdf", _scanned_pdf(), "application/pdf")),
                          ("files", ("photo.png", _page_png(), "image/png"))])
        assert r.status_code == 200, r.text
        items = c.get("/api/v1/candidates", headers=h).json()["items"]
        statuses = sorted(x["status"] for x in items)
        assert statuses == ["duplicate", "ready"]  # same CV twice: the second is caught as a duplicate
        ready = next(x for x in items if x["status"] == "ready")
        assert "ocr_used" in ready["flags"]
        assert c.get("/api/v1/candidates", headers=h, params={"q": "spark"}).json()["total"] == 1


def test_ocr_off_gives_a_clear_failure(env):
    env(ocr_engine="off")
    h = {"X-Tenant-ID": "t-ocr-off"}
    with TestClient(app) as c:
        c.post("/api/v1/candidates/upload", headers=h, files=[("files", ("scan.pdf", _scanned_pdf(), "application/pdf"))])
        item = c.get("/api/v1/candidates", headers=h).json()["items"][0]
        assert item["status"] == "failed" and "OCR is off" in item["error"]
        r = c.post("/api/v1/candidates/upload", headers=h, files=[("files", ("photo.png", _page_png(), "image/png"))])
        assert r.status_code == 200  # accepted, then fails in processing with a clear reason


def test_azure_ocr_client(env):
    env(ocr_engine="azure", azure_docintel_endpoint="https://example.cognitiveservices.azure.com",
        azure_docintel_key="k")
    from nsm.ingestion import ocr

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.method == "POST":
            assert req.headers["Ocp-Apim-Subscription-Key"] == "k"
            assert "prebuilt-read:analyze" in str(req.url)
            return httpx.Response(202, headers={"Operation-Location": "https://example/op/1"})
        return httpx.Response(200, json={"status": "succeeded", "analyzeResult": {"content": "Line one\nLine two"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ocr._ocr_azure(b"%PDF-", "application/pdf", client=client) == "Line one\nLine two"
    assert len(calls) == 2
    assert ocr.engine_name() == "azure"


# --- workspace security: API keys -------------------------------------------------------

KEY_A, KEY_B = "nsm_test_key_for_a", "nsm_test_key_for_b"
KEYS = json.dumps({hashlib.sha256(KEY_A.encode()).hexdigest(): "ws-a",
                   hashlib.sha256(KEY_B.encode()).hexdigest(): "ws-b"})
CV = b"Asha Nair\nExperience\nEngineer, Q Ltd (2019 - present)\n- Built data pipelines in Python and Spark.\n"


def test_api_key_decides_the_workspace(env):
    env(auth_mode="api_key", api_keys=KEYS)
    a = {"Authorization": f"Bearer {KEY_A}"}
    b = {"X-API-Key": KEY_B}
    with TestClient(app) as c:
        assert c.get("/api/v1/candidates").status_code == 401
        assert c.get("/api/v1/candidates", headers={"Authorization": "Bearer wrong"}).status_code == 401
        # the old attack: just name another workspace in the header
        assert c.get("/api/v1/candidates", headers={"X-Tenant-ID": "ws-a"}).status_code == 401

        assert c.post("/api/v1/candidates/upload", headers=a,
                      files=[("files", ("a.txt", CV, "text/plain"))]).status_code == 200
        assert c.get("/api/v1/candidates", headers=a).json()["total"] == 1
        assert c.get("/api/v1/candidates", headers=b).json()["total"] == 0
        cid = c.get("/api/v1/candidates", headers=a).json()["items"][0]["id"]
        assert c.get(f"/api/v1/candidates/{cid}", headers=b).status_code == 404

        # key B cannot borrow workspace A by adding the header
        assert c.get("/api/v1/candidates", headers={**b, "X-Tenant-ID": "ws-a"}).status_code == 403
        assert c.get("/api/v1/candidates", headers={**a, "X-Tenant-ID": "ws-a"}).status_code == 200
        # health and readiness stay public
        assert c.get("/api/v1/health").status_code == 200
        assert c.get("/api/v1/health").json()["auth_mode"] == "api_key"


def test_repeated_bad_keys_are_slowed_down(env):
    env(auth_mode="api_key", api_keys=KEYS)
    with TestClient(app) as c:
        codes = [c.get("/api/v1/candidates", headers={"Authorization": f"Bearer guess{i}"}).status_code
                 for i in range(25)]
    assert codes[0] == 401 and codes[-1] == 429


# --- workspace security: Entra ID / OIDC tokens -----------------------------------------

@pytest.fixture
def rsa_keys(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa

    from nsm import security

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(security, "_signing_key", lambda _t: key.public_key())
    return key


ISS = "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0"
AUD = "api://nsm-test"


def _token(key, **over) -> str:  # noqa: ANN001
    import jwt

    claims = {"iss": ISS, "aud": AUD, "exp": int(time.time()) + 600, "oid": "user-1", "tid": "dir-1"} | over
    return jwt.encode(claims, key, algorithm="RS256")


def test_entra_token_decides_the_workspace(env, rsa_keys):
    env(auth_mode="entra", oidc_issuer=ISS, oidc_audience=AUD, oidc_tenant_map=json.dumps({"dir-1": "ws-entra"}))
    with TestClient(app) as c:
        ok = {"Authorization": f"Bearer {_token(rsa_keys)}"}
        assert c.get("/api/v1/candidates", headers=ok).status_code == 200
        assert c.get("/api/v1/candidates", headers={**ok, "X-Tenant-ID": "ws-other"}).status_code == 403
        for bad in (_token(rsa_keys, aud="api://someone-else"), _token(rsa_keys, exp=int(time.time()) - 3600),
                    _token(rsa_keys, iss="https://evil.example/v2.0")):
            assert c.get("/api/v1/candidates", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
        # a directory that is not mapped to a workspace is refused
        other = {"Authorization": f"Bearer {_token(rsa_keys, tid='dir-unknown')}"}
        assert c.get("/api/v1/candidates", headers=other).status_code == 403


def test_entra_token_signed_by_another_key_is_refused(env, rsa_keys):
    from cryptography.hazmat.primitives.asymmetric import rsa

    env(auth_mode="entra", oidc_issuer=ISS, oidc_audience=AUD)
    forged = _token(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    with TestClient(app) as c:
        assert c.get("/api/v1/candidates", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


# --- rate limits and quotas ---------------------------------------------------------------

def test_rate_limit_returns_429_with_retry_after(env):
    env(rate_limit_per_minute=3)
    h = {"X-Tenant-ID": "t-rate"}
    with TestClient(app) as c:
        codes = [c.get("/api/v1/candidates", headers=h).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = c.get("/api/v1/candidates", headers=h)
        assert int(r.headers["Retry-After"]) >= 1
        # another workspace has its own allowance
        assert c.get("/api/v1/candidates", headers={"X-Tenant-ID": "t-rate-2"}).status_code == 200


def test_daily_upload_quota(env):
    env(daily_upload_quota=2)
    h = {"X-Tenant-ID": "t-quota"}
    files = [("files", (f"cv{i}.txt", CV + str(i).encode(), "text/plain")) for i in range(3)]
    with TestClient(app) as c:
        r = c.post("/api/v1/candidates/upload", headers=h, files=files)
        assert r.status_code == 429 and "quota" in r.json()["detail"]
        assert c.post("/api/v1/candidates/upload", headers=h, files=files[:2]).status_code == 200
        assert c.post("/api/v1/candidates/upload", headers=h, files=files[2:]).status_code == 429


# --- readiness ------------------------------------------------------------------------------

def test_readiness_checks_dependencies(env):
    with TestClient(app) as c:
        r = c.get("/api/v1/ready")
        assert r.status_code == 200 and r.json()["status"] == "ready"
        assert {"database", "storage", "ocr", "auth", "models"} <= set(r.json()["checks"])
    env(auth_mode="api_key", api_keys="")  # misconfigured: nobody could sign in
    with TestClient(app) as c:
        r = c.get("/api/v1/ready")
        assert r.status_code == 503 and r.json()["checks"]["auth"]["ok"] is False
