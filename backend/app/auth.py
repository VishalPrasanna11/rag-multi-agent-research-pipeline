"""Identity: verified OIDC JWT `sub` (Clerk/Auth0 via JWKS) or a signed anonymous cookie.

- When AUTH_JWKS_URL + AUTH_ISSUER are set, a valid Bearer token is required on every user-data route
  (anonymous access is refused unless ALLOW_ANONYMOUS is explicitly true).
- When auth is not configured, each browser gets a signed `ei_anon` cookie and its data is scoped to it.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings
from app.db import User, session_scope
from app.errors import AppError

ANON_COOKIE = "ei_anon"


@dataclass(frozen=True)
class Identity:
    sub: str
    anonymous: bool
    email: str | None = None


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        key = _jwks_client(s.AUTH_JWKS_URL).get_signing_key_from_jwt(token).key
        options: Any = {"require": ["exp", "iat", "sub"], "verify_aud": bool(s.AUTH_AUDIENCE)}
        return jwt.decode(
            token,
            key,
            algorithms=["RS256", "ES256"],
            issuer=s.AUTH_ISSUER,
            audience=s.AUTH_AUDIENCE or None,
            options=options,
            leeway=30,
        )
    except jwt.PyJWKClientError as e:
        raise AppError("upstream_unavailable", "Could not fetch signing keys", 503, retryable=True) from e
    except jwt.PyJWTError as e:
        raise AppError("unauthorized", f"Invalid token: {type(e).__name__}", 401) from e


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().SESSION_SECRET, salt="anon-id")


def _ensure_user(identity: Identity) -> None:
    with session_scope() as sess:
        if sess.get(User, identity.sub) is None:
            sess.add(User(id=identity.sub, email=identity.email, is_anonymous=identity.anonymous))


async def current_identity(request: Request, response: Response) -> Identity:
    s = get_settings()
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        if not s.auth_configured:
            raise AppError("unauthorized", "Bearer tokens are not accepted: auth is not configured", 401)
        claims = verify_token(authz[7:].strip())
        ident = Identity(sub=str(claims["sub"]), anonymous=False, email=claims.get("email"))
        _ensure_user(ident)
        return ident

    if s.auth_configured and not s.ALLOW_ANONYMOUS:
        raise AppError("unauthorized", "Sign in required", 401, headers={"WWW-Authenticate": "Bearer"})
    if not s.ALLOW_ANONYMOUS:
        raise AppError("unauthorized", "Sign in required", 401)

    raw = request.cookies.get(ANON_COOKIE)
    anon_id = None
    if raw:
        try:
            anon_id = _serializer().loads(raw)
        except BadSignature:
            anon_id = None
    if not anon_id:
        anon_id = f"anon:{uuid.uuid4().hex}"
        response.set_cookie(
            ANON_COOKIE,
            _serializer().dumps(anon_id),
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
            secure=s.is_production,
        )
    ident = Identity(sub=str(anon_id), anonymous=True)
    _ensure_user(ident)
    return ident


# ---------------------------------------------------------------- rate limiting (per identity, per process)

_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_rate(identity: Identity, bucket: str, limit_per_hour: int) -> None:
    now = time.time()
    q = _buckets[f"{bucket}:{identity.sub}"]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= limit_per_hour:
        retry = int(3600 - (now - q[0])) + 1
        raise AppError(
            "rate_limited",
            f"Limit of {limit_per_hour} {bucket} per hour reached",
            429,
            retryable=True,
            headers={"Retry-After": str(retry)},
        )
    q.append(now)


def reset_rate_limits() -> None:
    _buckets.clear()
