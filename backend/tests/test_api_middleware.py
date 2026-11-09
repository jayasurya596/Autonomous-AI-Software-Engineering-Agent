"""API request-boundary, error-safety, and middleware checks."""

import asyncio
import uuid
from typing import Annotated, Any

import httpx
from app.core.auth import FirebasePrincipal, get_current_user
from app.core.settings import ApiSettings
from app.main import app, create_app
from fastapi import Body, HTTPException


def send_request(target_app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def perform() -> httpx.Response:
        transport = httpx.ASGITransport(app=target_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(perform())


def test_request_id_and_security_headers_are_added_or_preserved() -> None:
    generated = send_request(app, "GET", "/health")
    supplied_id = str(uuid.uuid4())
    preserved = send_request(app, "GET", "/health", headers={"X-Request-ID": supplied_id})

    assert generated.status_code == 200
    assert str(uuid.UUID(generated.headers["x-request-id"])) == generated.headers["x-request-id"]
    assert preserved.headers["x-request-id"] == supplied_id
    assert generated.headers["x-content-type-options"] == "nosniff"
    assert generated.headers["x-frame-options"] == "DENY"
    assert generated.headers["referrer-policy"] == "no-referrer"


def test_cors_preflight_uses_only_configured_origins() -> None:
    test_app = create_app(ApiSettings(cors_origins="https://app.example"))
    allowed = send_request(
        test_app,
        "OPTIONS",
        "/api/v1/auth/me",
        headers={
            "Origin": "https://app.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-request-id",
        },
    )
    blocked = send_request(
        test_app,
        "OPTIONS",
        "/api/v1/auth/me",
        headers={
            "Origin": "https://unapproved.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example"
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-origin" not in blocked.headers


def test_request_size_and_rate_limits_return_safe_responses() -> None:
    test_app = create_app(
        ApiSettings(
            cors_origins="http://testserver",
            api_rate_limit_requests=2,
            api_rate_limit_window_seconds=60,
            max_request_body_bytes=32,
        )
    )
    too_large = send_request(
        test_app,
        "POST",
        "/api/v1/users/profile",
        content=b"x" * 64,
        headers={
            "Content-Type": "application/json",
            "Origin": "http://testserver",
        },
    )
    first_api_call = send_request(test_app, "GET", "/api/v1/auth/me")
    rate_limited = send_request(test_app, "GET", "/api/v1/auth/me")

    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "request_too_large"
    assert too_large.headers["x-request-id"]
    assert too_large.headers["access-control-allow-origin"] == "http://testserver"
    assert first_api_call.status_code == 401
    assert first_api_call.headers["x-ratelimit-remaining"] == "0"
    assert rate_limited.status_code == 429
    assert rate_limited.headers["retry-after"]
    assert rate_limited.json()["error"]["code"] == "rate_limit_exceeded"


def test_rate_limit_uses_the_validated_cloudflare_client_ip() -> None:
    test_app = create_app(
        ApiSettings(
            cors_origins="http://testserver",
            api_rate_limit_requests=1,
            api_rate_limit_window_seconds=60,
        )
    )
    first_client = send_request(
        test_app,
        "GET",
        "/api/v1/auth/me",
        headers={"X-Agent-Client-IP": "203.0.113.10"},
    )
    second_client = send_request(
        test_app,
        "GET",
        "/api/v1/auth/me",
        headers={"X-Agent-Client-IP": "203.0.113.11"},
    )
    first_client_again = send_request(
        test_app,
        "GET",
        "/api/v1/auth/me",
        headers={"X-Agent-Client-IP": "203.0.113.10"},
    )

    assert first_client.status_code == 401
    assert second_client.status_code == 401
    assert first_client_again.status_code == 429


def test_validation_and_unexpected_errors_do_not_echo_sensitive_values() -> None:
    test_app = create_app(ApiSettings(cors_origins="http://testserver"))

    @test_app.post("/_test/validation")
    def validate_secret(secret: Annotated[str, Body(min_length=24)]) -> dict[str, bool]:
        return {"accepted": bool(secret)}

    @test_app.get("/_test/not-found")
    def private_not_found() -> None:
        raise HTTPException(status_code=404, detail="private-resource-name")

    @test_app.get("/_test/conflict")
    def safe_conflict() -> None:
        raise HTTPException(status_code=409, detail="The reviewed source is stale.")

    @test_app.get("/_test/crash")
    def private_crash() -> None:
        raise RuntimeError("secret-value-must-not-leak")

    invalid = send_request(
        test_app, "POST", "/_test/validation", json={"secret": "private-token-value"}
    )
    not_found = send_request(test_app, "GET", "/_test/not-found")
    conflict = send_request(test_app, "GET", "/_test/conflict")
    failed = send_request(test_app, "GET", "/_test/crash")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert "private-token-value" not in invalid.text
    assert not_found.status_code == 404
    assert "private-resource-name" not in not_found.text
    assert conflict.status_code == 409
    assert conflict.json()["error"]["message"] == "The reviewed source is stale."
    assert failed.status_code == 500
    assert "secret-value-must-not-leak" not in failed.text
    assert failed.json()["error"]["request_id"] == failed.headers["x-request-id"]


def test_workspace_creation_requires_a_valid_idempotency_key() -> None:
    payload = {"name": "Test workspace", "description": ""}
    principal = FirebasePrincipal(
        uid="idempotency-header-test",
        email="idempotency@example.test",
        email_verified=True,
    )
    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        missing = send_request(app, "POST", "/api/v1/workspaces", json=payload)
        malformed = send_request(
            app,
            "POST",
            "/api/v1/workspaces",
            json=payload,
            headers={"Idempotency-Key": "short"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert missing.status_code == 422
    assert malformed.status_code == 422
    assert missing.json()["error"]["code"] == "validation_error"
    assert "input" not in missing.text.lower()
