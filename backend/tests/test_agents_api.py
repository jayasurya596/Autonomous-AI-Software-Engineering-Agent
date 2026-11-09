"""Authenticated API coverage for local model health and workflow runs."""

import asyncio

import httpx
from app.api.agents import get_agent_workflow, get_ollama_adapter
from app.core.auth import FirebasePrincipal, get_current_user
from app.main import app
from app.schemas.agents import OllamaHealthResponse
from app.services.agent_workflow import AgentWorkflow
from app.services.ollama import InvalidModelOutputError, OllamaUnavailableError
from test_agent_workflow import FakeModel


def send_request(method: str, path: str, body: dict[str, object] | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def use_test_identity() -> None:
    principal = FirebasePrincipal(
        uid="phase5-test-owner",
        email="phase5@example.test",
        email_verified=True,
    )
    app.dependency_overrides[get_current_user] = lambda: principal


def test_agent_run_requires_verified_identity() -> None:
    response = send_request(
        "POST", "/api/v1/agents/runs", {"goal": "Create a basic Python API health endpoint."}
    )
    assert response.status_code == 401


def test_repository_analysis_requires_verified_identity() -> None:
    response = send_request(
        "POST",
        "/api/v1/repositories/projects/example-project/analyze",
        {"repository_directory": "sample"},
    )
    assert response.status_code == 401


def test_repository_test_requires_verified_identity() -> None:
    response = send_request(
        "POST",
        "/api/v1/repositories/projects/example-project/test",
        {"repository_directory": "sample", "runtime": "python", "changes": []},
    )
    assert response.status_code == 401


def test_persistent_execution_and_memory_require_verified_identity() -> None:
    execution = send_request(
        "POST",
        "/api/v1/executions",
        {"goal": "Create a persisted agent execution."},
    )
    memory = send_request(
        "GET",
        "/api/v1/projects/example-project/memory",
    )
    assert execution.status_code == 401
    assert memory.status_code == 401


def test_agent_run_returns_structured_workflow_result() -> None:
    use_test_identity()
    app.dependency_overrides[get_agent_workflow] = lambda: AgentWorkflow(FakeModel())
    try:
        response = send_request(
            "POST", "/api/v1/agents/runs", {"goal": "Create a basic Python API health endpoint."}
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_agent_workflow, None)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["model"] == "test-model"
    assert result["plan"]["tasks"][1]["depends_on"] == ["task_1"]
    assert result["completed_agents"] == [
        "requirement_analyst",
        "architecture",
        "planner",
        "coding",
        "code_review",
        "testing",
        "documentation",
    ]
    assert len(result["agent_messages"]) == 7
    assert "phase5-test-owner" not in response.text


def test_ollama_health_is_authenticated_and_safe() -> None:
    use_test_identity()

    class FakeAdapter:
        async def health_check(self) -> OllamaHealthResponse:
            return OllamaHealthResponse(
                status="model_not_found",
                runtime_available=True,
                configured_model="llama3.2:1b",
                model_available=False,
                available_models=[],
            )

    app.dependency_overrides[get_ollama_adapter] = lambda: FakeAdapter()
    try:
        response = send_request("GET", "/api/v1/agents/health")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_ollama_adapter, None)

    assert response.status_code == 200
    assert response.json()["status"] == "model_not_found"
    assert "127.0.0.1:11434" not in response.text


def test_agent_run_maps_unavailable_model_to_safe_service_error() -> None:
    use_test_identity()

    class UnavailableWorkflow:
        async def run(self, goal: str) -> None:
            del goal
            raise OllamaUnavailableError("internal endpoint details")

    app.dependency_overrides[get_agent_workflow] = lambda: UnavailableWorkflow()
    try:
        response = send_request(
            "POST", "/api/v1/agents/runs", {"goal": "Create a basic Python API health endpoint."}
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_agent_workflow, None)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "internal endpoint details" not in response.text


def test_agent_run_maps_invalid_model_output_to_safe_gateway_error() -> None:
    use_test_identity()

    class InvalidOutputWorkflow:
        async def run(self, goal: str) -> None:
            del goal
            raise InvalidModelOutputError("private model response contents")

    app.dependency_overrides[get_agent_workflow] = lambda: InvalidOutputWorkflow()
    try:
        response = send_request(
            "POST", "/api/v1/agents/runs", {"goal": "Create a basic Python API health endpoint."}
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_agent_workflow, None)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_error"
    assert "private model response contents" not in response.text
