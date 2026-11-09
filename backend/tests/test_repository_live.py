"""Opt-in live check for local repository analysis and code generation."""

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
    os.getenv("RUN_OLLAMA_LIVE_TESTS") != "1",
    reason="Set RUN_OLLAMA_LIVE_TESTS=1 to run against local Ollama.",
)


def test_live_repository_analysis_and_change_proposal(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    project_dir = root / "sample"
    project_dir.mkdir(parents=True)
    source = project_dir / "app.py"
    source.write_text("def main():\n    print('hello')\n", encoding="utf-8")
    live_app = create_app(ApiSettings(agent_repositories_root=str(root)))
    live_app.dependency_overrides[get_current_user] = lambda: FirebasePrincipal(
        uid="repository-live-test",
        email="repository-live@example.test",
        email_verified=True,
    )
    live_app.dependency_overrides[get_owned_project] = lambda: {
        "name": "Sample CLI",
        "purpose": "A tiny Python command-line project.",
        "language": "Python",
        "framework": None,
    }

    async def verify() -> None:
        try:
            transport = httpx.ASGITransport(app=live_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                analyze = await client.post(
                    "/api/v1/repositories/projects/sample-project/analyze",
                    json={"repository_directory": "sample"},
                )
                assert analyze.status_code == 200, analyze.text
                assert analyze.json()["analysis"]["relevant_files"]

                changes = await client.post(
                    "/api/v1/repositories/projects/sample-project/changes",
                    json={
                        "repository_directory": "sample",
                        "goal": "Add a simple argparse command interface.",
                    },
                )
                assert changes.status_code == 200, changes.text
                result = changes.json()
                assert result["proposal"]["files"]
                assert result["agent_message"]["warnings"]
                assert source.read_text(encoding="utf-8") == "def main():\n    print('hello')\n"
        finally:
            await live_app.state.ollama_adapter.close()

    asyncio.run(verify())
