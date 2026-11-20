import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings


@pytest.fixture
def jwt_env(monkeypatch: pytest.MonkeyPatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = get_settings()
    monkeypatch.setattr(s, "AUTH_JWKS_URL", "https://issuer.test/.well-known/jwks.json")
    monkeypatch.setattr(s, "AUTH_ISSUER", "https://issuer.test")
    monkeypatch.setattr(s, "ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(
        auth,
        "_jwks_client",
        lambda url: SimpleNamespace(get_signing_key_from_jwt=lambda t: SimpleNamespace(key=key.public_key())),
    )

    def token(sub: str, iss: str = "https://issuer.test", exp: int = 300) -> str:
        now = int(time.time())
        return jwt.encode({"sub": sub, "iss": iss, "iat": now, "exp": now + exp}, key, algorithm="RS256")

    return token


def test_auth_enforced_and_scoped(client: TestClient, jwt_env) -> None:
    assert client.get("/api/v1/runs").json()["error"]["code"] == "unauthorized"
    bad = client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {jwt_env('u1', iss='https://evil.test')}"}
    )
    assert bad.status_code == 401
    expired = client.get("/api/v1/runs", headers={"Authorization": f"Bearer {jwt_env('u1', exp=-120)}"})
    assert expired.status_code == 401
    h1 = {"Authorization": f"Bearer {jwt_env('user_1')}"}
    h2 = {"Authorization": f"Bearer {jwt_env('user_2')}"}
    assert client.post("/api/v1/watchlist", json={"ticker": "AAPL"}, headers=h1).status_code == 201
    assert [w["ticker"] for w in client.get("/api/v1/watchlist", headers=h1).json()] == ["AAPL"]
    assert client.get("/api/v1/watchlist", headers=h2).json() == []
    assert client.get("/api/v1/health").status_code == 200  # public
