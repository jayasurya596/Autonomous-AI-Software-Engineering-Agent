"""Public readiness is distinct from process liveness and hides infrastructure details."""

import asyncio

import httpx
from app.core.settings import ApiSettings
from app.main import create_app


class AvailableModels:
    async def available_models(self) -> list[str]:
        return ["llama3.2:1b", "nomic-embed-text"]


def test_liveness_stays_up_when_required_services_are_not_ready() -> None:
    test_app = create_app(ApiSettings())
    test_app.state.ollama_adapter = AvailableModels()

    async def verify() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health"), await client.get("/api/v1/monitoring/ready")

    live_app_response, readiness_response = asyncio.run(verify())

    assert live_app_response.status_code == 200
    assert readiness_response.status_code == 503
    assert readiness_response.json() == {
        "status": "not_ready",
        "checks": {"database": "not_configured", "model": "ready"},
    }
