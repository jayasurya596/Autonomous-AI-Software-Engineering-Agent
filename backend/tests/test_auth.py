import asyncio

import httpx
from app.core import auth as auth_core
from app.core.auth import FirebasePrincipal
from app.main import app
from firebase_admin import auth


def get(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(request())


def test_auth_me_requires_bearer_token() -> None:
    response = get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_me_reports_missing_firebase_configuration(monkeypatch) -> None:
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    response = get("/api/v1/auth/me", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 503


def test_auth_emulator_is_rejected_outside_development(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-software-agent")
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    response = get("/api/v1/auth/me", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 503


def test_auth_me_rejects_invalid_firebase_token(monkeypatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-software-agent")
    monkeypatch.setattr(auth_core, "_get_firebase_app", lambda: object())

    def reject_token(_token, _app):
        raise auth.InvalidIdTokenError("invalid")

    monkeypatch.setattr(auth_core, "_verify_id_token", reject_token)
    response = get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid-token"})

    assert response.status_code == 401


def test_auth_me_returns_verified_identity(monkeypatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-software-agent")
    monkeypatch.setattr(auth_core, "_get_firebase_app", lambda: object())
    monkeypatch.setattr(
        auth_core,
        "_verify_id_token",
        lambda _token, _app: {
            "uid": "user-123",
            "email": "developer@example.test",
            "email_verified": True,
            "name": "Developer",
        },
    )
    response = get("/api/v1/auth/me", headers={"Authorization": "Bearer valid-test-token"})

    assert response.status_code == 200
    assert response.json() == {
        "uid": "user-123",
        "email": "developer@example.test",
        "email_verified": True,
        "display_name": "Developer",
    }


def test_auth_me_rejects_unverified_email(monkeypatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-software-agent")
    monkeypatch.setattr(auth_core, "_get_firebase_app", lambda: object())
    monkeypatch.setattr(
        auth_core,
        "_verify_id_token",
        lambda _token, _app: {
            "uid": "user-123",
            "email": "developer@example.test",
            "email_verified": False,
        },
    )
    response = get("/api/v1/auth/me", headers={"Authorization": "Bearer unverified-test-token"})

    assert response.status_code == 403


def test_principal_ignores_untrusted_extra_claims() -> None:
    principal = FirebasePrincipal.model_validate(
        {
            "uid": "user-123",
            "email_verified": True,
            "admin": True,
        }
    )

    assert "admin" not in principal.model_dump()
