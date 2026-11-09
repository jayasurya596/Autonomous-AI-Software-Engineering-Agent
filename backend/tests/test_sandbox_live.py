"""Opt-in API check against a real local Docker sandbox image."""

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from app.api.repositories import get_owned_project
from app.core.auth import FirebasePrincipal, get_current_user
from app.core.settings import ApiSettings
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DOCKER_LIVE_TESTS") != "1",
    reason="Set RUN_DOCKER_LIVE_TESTS=1 after building the sandbox image.",
)


def test_live_docker_test_runs_on_isolated_proposal(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    project = root / "sample"
    tests = project / "tests"
    tests.mkdir(parents=True)
    source = project / "app.py"
    source.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    (tests / "test_app.py").write_text(
        "from app import add\n\ndef test_addition():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    node_project = root / "node-sample"
    (node_project / "test").mkdir(parents=True)
    (node_project / "test" / "sample.test.js").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "test('basic arithmetic', () => assert.equal(2 + 2, 4));\n",
        encoding="utf-8",
    )
    live_app = create_app(
        ApiSettings(agent_repositories_root=str(root), sandbox_timeout_seconds=60)
    )
    live_app.dependency_overrides[get_current_user] = lambda: FirebasePrincipal(
        uid="sandbox-live-test",
        email="sandbox-live@example.test",
        email_verified=True,
    )
    live_app.dependency_overrides[get_owned_project] = lambda: {"name": "Sandbox sample"}

    async def verify() -> None:
        try:
            transport = httpx.ASGITransport(app=live_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/repositories/projects/sample-project/test",
                    json={
                        "repository_directory": "sample",
                        "runtime": "python",
                        "changes": [
                            {
                                "path": "app.py",
                                "content": "def add(left, right):\n    return left + right\n",
                                "rationale": "Fix addition behavior.",
                            }
                        ],
                    },
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["status"] == "passed", result["output"]
                assert result["exit_code"] == 0
                assert result["command"] == [
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ]
                assert source.read_text(encoding="utf-8") == (
                    "def add(left, right):\n    return left - right\n"
                )
                node_response = await client.post(
                    "/api/v1/repositories/projects/sample-project/test",
                    json={"repository_directory": "node-sample", "runtime": "node"},
                )
                assert node_response.status_code == 200, node_response.text
                assert node_response.json()["status"] == "passed", node_response.json()["output"]
        finally:
            await live_app.state.ollama_adapter.close()

    asyncio.run(verify())
