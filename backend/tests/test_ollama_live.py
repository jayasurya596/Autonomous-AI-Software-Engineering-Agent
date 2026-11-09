"""Opt-in live test for a running local Ollama model and full LangGraph run."""

import asyncio
import os

import httpx
import pytest
from app.core.auth import FirebasePrincipal, get_current_user
from app.core.settings import ApiSettings
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OLLAMA_LIVE_TESTS") != "1",
    reason="Set RUN_OLLAMA_LIVE_TESTS=1 to run against local Ollama.",
)


def test_live_ollama_and_full_agent_graph() -> None:
    live_app = create_app(ApiSettings())
    live_app.dependency_overrides[get_current_user] = lambda: FirebasePrincipal(
        uid="ollama-live-test",
        email="ollama-live@example.test",
        email_verified=True,
    )

    async def verify() -> None:
        try:
            transport = httpx.ASGITransport(app=live_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                health_response = await client.get("/api/v1/agents/health")
                assert health_response.status_code == 200
                assert health_response.json()["status"] == "ready"

                run_response = await client.post(
                    "/api/v1/agents/runs",
                    json={
                        "goal": "Create a Python command-line calculator that adds two numbers "
                        "and handles invalid input."
                    },
                )
                assert run_response.status_code == 200, run_response.text
                result = run_response.json()
                assert result["requirements"]["functional_requirements"]
                assert result["plan"]["tasks"]
                assert result["coding_brief"]["implementation_steps"]
                assert result["coding_brief"]["requires_human_approval"] is True
                assert result["completed_agents"] == [
                    "requirement_analyst",
                    "architecture",
                    "planner",
                    "coding",
                    "code_review",
                    "testing",
                    "documentation",
                ]
        finally:
            await live_app.state.ollama_adapter.close()

    asyncio.run(verify())
