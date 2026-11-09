"""Firestore client configuration for emulator and production environments."""

from app.core.firestore import get_firestore_client
from google.auth.credentials import AnonymousCredentials


def test_emulator_client_uses_anonymous_credentials(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-software-agent")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    captured: dict[str, object] = {}

    def create_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return captured

    monkeypatch.setattr("app.core.firestore.google_firestore.Client", create_client)

    client = get_firestore_client()

    assert client is captured
    assert captured["project"] == "demo-software-agent"
    assert isinstance(captured["credentials"], AnonymousCredentials)


def test_emulator_client_remains_blocked_in_production(monkeypatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-software-agent")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")

    try:
        get_firestore_client()
    except HTTPException as exc:
        assert exc.status_code == 503
        assert "only in development or test" in exc.detail
    else:
        raise AssertionError("Production must reject the Firestore emulator.")
