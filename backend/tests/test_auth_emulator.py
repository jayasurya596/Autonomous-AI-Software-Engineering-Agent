"""End-to-end authentication check for a running Firebase Auth Emulator."""

import asyncio
import os
import uuid

import httpx
import pytest
from app.main import app

EMULATOR_HOST = os.getenv("FIREBASE_AUTH_EMULATOR_HOST", "")
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
API_KEY = "demo-api-key"

pytestmark = pytest.mark.skipif(
    not EMULATOR_HOST or not PROJECT_ID,
    reason="Set FIREBASE_AUTH_EMULATOR_HOST and FIREBASE_PROJECT_ID to run emulator integration.",
)


def test_register_verify_sign_in_and_verify_backend_identity(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    emulator_url = f"http://{EMULATOR_HOST.removeprefix('http://').removeprefix('https://')}"
    email = f"phase2-{uuid.uuid4().hex}@example.test"
    password = "LocalTestPassword42!"
    client = httpx.Client(base_url=emulator_url, timeout=10)

    created = client.post(
        f"/identitytoolkit.googleapis.com/v1/accounts:signUp?key={API_KEY}",
        json={"email": email, "password": password, "returnSecureToken": True},
    )
    created.raise_for_status()
    unverified_token = created.json()["idToken"]

    sent = client.post(
        f"/identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={API_KEY}",
        json={"requestType": "VERIFY_EMAIL", "idToken": unverified_token},
    )
    sent.raise_for_status()

    codes = client.get(f"/emulator/v1/projects/{PROJECT_ID}/oobCodes")
    codes.raise_for_status()
    verification_code = next(
        item["oobCode"]
        for item in reversed(codes.json().get("oobCodes", []))
        if item.get("email") == email and item.get("requestType") == "VERIFY_EMAIL"
    )
    verification = client.get(
        "/emulator/action",
        params={
            "mode": "verifyEmail",
            "lang": "en",
            "oobCode": verification_code,
            "apiKey": API_KEY,
        },
    )
    verification.raise_for_status()

    signed_in = client.post(
        f"/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}",
        json={"email": email, "password": password, "returnSecureToken": True},
    )
    signed_in.raise_for_status()
    id_token = signed_in.json()["idToken"]

    async def read_verified_identity() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
            return await api_client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {id_token}"}
            )

    identity = asyncio.run(read_verified_identity())

    assert identity.status_code == 200
    assert identity.json()["email"] == email
    assert identity.json()["email_verified"] is True
