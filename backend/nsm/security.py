"""Who is calling, and which workspace (tenant) they may use. Also per-workspace rate limits.

Version 1 took the workspace from the X-Tenant-ID header, which the caller chooses, so anyone could read
another workspace's CVs by changing one header. Now the workspace comes from a credential:

- NSM_AUTH_MODE=none     local single-user build only. The header still picks the workspace. Never expose it.
- NSM_AUTH_MODE=api_key  each API key belongs to exactly one workspace. Only SHA-256 hashes of keys are stored
                         (NSM_API_KEYS = {"<sha256 hex>": "<workspace>"}); make keys with scripts/make_api_key.py.
- NSM_AUTH_MODE=entra    a Microsoft Entra ID (or any OIDC) bearer token. Signature, issuer, audience and expiry
                         are checked; the workspace comes from a claim (default "tid"), optionally mapped.

In api_key and entra modes an X-Tenant-ID header that names a different workspace is refused (403), so a
client bug or tampering is visible instead of silently ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

from nsm.config import get_settings

log = logging.getLogger("nsm.security")
TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PUBLIC_PATHS = {"/api/v1/health", "/api/v1/ready"}


class AuthError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class Principal:
    tenant: str
    subject: str  # who: key id or token subject (for the audit log)
    method: str   # none | api_key | entra


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _key_table(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        table = json.loads(raw)
    except ValueError:
        log.error("NSM_API_KEYS is not valid JSON; no API key will be accepted")
        return {}
    return {str(k).lower(): str(v) for k, v in table.items() if TENANT_RE.match(str(v))}


def _bearer(headers) -> str:  # noqa: ANN001
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("x-api-key") or "").strip()


def _from_api_key(token: str) -> Principal:
    if not token:
        raise AuthError(401, "missing API key")
    digest = hash_key(token)
    for stored, tenant in _key_table(get_settings().api_keys).items():
        if hmac.compare_digest(stored, digest):
            return Principal(tenant=tenant, subject=f"key:{digest[:8]}", method="api_key")
    raise AuthError(401, "invalid API key")


@lru_cache(maxsize=4)
def _jwks_client(url: str):  # noqa: ANN202
    import jwt

    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _jwks_url() -> str:
    s = get_settings()
    if s.oidc_jwks_url:
        return s.oidc_jwks_url
    issuer = s.oidc_issuer.rstrip("/")
    if "login.microsoftonline.com" in issuer:
        return issuer.removesuffix("/v2.0") + "/discovery/v2.0/keys"
    import httpx

    return httpx.get(issuer + "/.well-known/openid-configuration", timeout=10).json()["jwks_uri"]


def _signing_key(token: str):  # noqa: ANN202
    """Separate so tests can supply a local key instead of a network JWKS endpoint."""
    return _jwks_client(_jwks_url()).get_signing_key_from_jwt(token).key


def _from_entra(token: str) -> Principal:
    import jwt

    s = get_settings()
    if not token:
        raise AuthError(401, "missing bearer token")
    if not (s.oidc_issuer and s.oidc_audience):
        raise AuthError(500, "login is not configured (NSM_OIDC_ISSUER / NSM_OIDC_AUDIENCE)")
    try:
        claims = jwt.decode(token, _signing_key(token), algorithms=["RS256"], audience=s.oidc_audience,
                            issuer=s.oidc_issuer, options={"require": ["exp", "iss", "aud"]}, leeway=60)
    except jwt.PyJWTError as e:
        raise AuthError(401, f"invalid token: {e.__class__.__name__}") from None
    raw = str(claims.get(s.oidc_tenant_claim) or "")
    tenant = raw
    if s.oidc_tenant_map.strip():
        try:
            tenant = json.loads(s.oidc_tenant_map).get(raw, "")
        except ValueError:
            tenant = ""
    if not tenant or not TENANT_RE.match(tenant):
        raise AuthError(403, "this account is not linked to a workspace")
    return Principal(tenant=tenant, subject=str(claims.get("oid") or claims.get("sub") or "user"), method="entra")


def authenticate(headers) -> Principal:  # noqa: ANN001
    """Resolve the caller and workspace from request headers. Raises AuthError."""
    s = get_settings()
    mode = s.auth_mode.lower()
    asked = headers.get("x-tenant-id")
    if mode == "none":
        t = asked or s.default_tenant
        if not TENANT_RE.match(t):
            raise AuthError(400, "invalid X-Tenant-ID")
        return Principal(tenant=t, subject="local", method="none")
    if mode == "api_key":
        p = _from_api_key(_bearer(headers))
    elif mode == "entra":
        p = _from_entra(_bearer(headers))
    else:
        raise AuthError(500, f"unknown NSM_AUTH_MODE: {s.auth_mode}")
    if asked and asked != p.tenant:
        raise AuthError(403, "X-Tenant-ID does not match your credentials")
    return p


# ---------------------------------------------------------------------------------------
# rate limits (token bucket, in memory, per process)
# ---------------------------------------------------------------------------------------

class RateLimiter:
    """Allows `per_minute` requests per key, refilled continuously. Each server instance keeps its own
    buckets, so with N instances the effective limit is up to N times higher; the daily quotas, which are
    counted in the database, are exact."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def hit(self, scope: str, key: str, per_minute: int) -> float:
        """Returns 0 if allowed, else the seconds to wait."""
        if per_minute <= 0:
            return 0.0
        now = time.monotonic()
        rate = per_minute / 60.0
        with self._lock:
            tokens, last = self._buckets.get((scope, key), (float(per_minute), now))
            tokens = min(float(per_minute), tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._buckets[(scope, key)] = (tokens - 1.0, now)
                return 0.0
            self._buckets[(scope, key)] = (tokens, now)
            return (1.0 - tokens) / rate

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


limiter = RateLimiter()
