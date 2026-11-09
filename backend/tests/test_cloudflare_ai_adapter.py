"""Cloudflare Workers AI transport and validation checks."""

import asyncio
import json

import httpx
from app.core.settings import ApiSettings
from app.schemas.agents import RequirementsSpec
from app.services.ollama import OllamaModelAdapter


def test_cloudflare_health_structured_generation_and_embeddings() -> None:
    calls: list[tuple[str, str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.url.host == "workers-ai.internal"
        assert "authorization" not in request.headers
        if request.method == "GET":
            assert request.url.params["model"] in {
                "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "@cf/baai/bge-base-en-v1.5",
            }
            return httpx.Response(200, json={"success": True, "result": {}})
        body = json.loads(request.content)
        if request.url.path.endswith("/v1/chat/completions"):
            assert body["response_format"]["type"] == "json_schema"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
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
                            }
                        }
                    ]
                },
            )
        assert request.url.path.endswith("/v1/embeddings")
        assert body["model"] == "@cf/baai/bge-base-en-v1.5"
        return httpx.Response(200, json={"data": [{"embedding": [0.25] * 768}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    adapter = OllamaModelAdapter(
        ApiSettings(
            ai_provider="cloudflare",
        ),
        http_client=client,
    )

    async def check() -> None:
        health = await adapter.health_check()
        assert health.status == "ready"
        assert health.available_models == [
            "@cf/baai/bge-base-en-v1.5",
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        ]
        output = await adapter.generate_structured("system", "goal", RequirementsSpec)
        assert output.summary == "Add an API health response."
        vector = await adapter.embed("memory")
        assert len(vector) == 768
        assert vector[0] == 0.25
        await adapter.close()

    asyncio.run(check())
    assert sum(path.endswith("/models/schema") for _method, path in calls) == 2
    assert any(path.endswith("/v1/chat/completions") for _method, path in calls)
    assert any(path.endswith("/v1/embeddings") for _method, path in calls)
