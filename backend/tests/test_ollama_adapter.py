"""Ollama SDK adapter checks against a local in-memory HTTP transport."""

import asyncio
import json

import httpx
import pytest
from app.core.settings import ApiSettings
from app.schemas.agents import RequirementsSpec
from app.services.ollama import InvalidModelOutputError, OllamaModelAdapter
from ollama import AsyncClient


def make_adapter(response_content: str) -> OllamaModelAdapter:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"model": "llama3.2:1b", "name": "llama3.2:1b"}]},
            )
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "llama3.2:1b"
        assert body["stream"] is False
        assert body["format"]["type"] == "object"
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "message": {"role": "assistant", "content": response_content},
            },
        )

    client = AsyncClient(transport=httpx.MockTransport(respond))
    return OllamaModelAdapter(ApiSettings(), client=client)


def test_ollama_health_and_structured_response() -> None:
    adapter = make_adapter(
        json.dumps(
            {
                "summary": "Add an API health response.",
                "functional_requirements": ["Return status."],
                "non_functional_requirements": [],
                "constraints": [],
                "assumptions": [],
                "risks": [],
                "open_questions": [],
            }
        )
    )

    async def check() -> None:
        health = await adapter.health_check()
        assert health.status == "ready"
        assert health.available_models == ["llama3.2:1b"]
        output = await adapter.generate_structured(
            "Return structured requirements.", "Add a health response.", RequirementsSpec
        )
        assert output.summary == "Add an API health response."
        await adapter.close()

    asyncio.run(check())


def test_ollama_adapter_rejects_invalid_model_json() -> None:
    adapter = make_adapter('{"summary": "missing required fields"}')

    async def check() -> None:
        with pytest.raises(InvalidModelOutputError):
            await adapter.generate_structured("system", "goal", RequirementsSpec)
        await adapter.close()

    asyncio.run(check())
