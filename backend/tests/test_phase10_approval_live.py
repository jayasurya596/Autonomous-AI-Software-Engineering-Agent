"""Opt-in PostgreSQL-backed approval, encryption, isolation and GitHub workflow check."""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.api import approvals as approvals_api
from app.api import github as github_api
from app.api.auth import get_current_user
from app.api.repositories import get_owned_project, get_sandbox_service
from app.core.auth import FirebasePrincipal
from app.core.settings import ApiSettings
from app.main import create_app
from app.persistence.models import ApprovalRecord
from app.services.github import GithubIntegration
from app.services.repositories import git_blob_sha
from cryptography.fernet import Fernet
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PHASE10_LIVE_TESTS") != "1",
    reason="Set RUN_PHASE10_LIVE_TESTS=1 and DATABASE_URL for the approval integration check.",
)


class PassingSandbox:
    def __init__(self) -> None:
        self.status = "passed"

    def run_repository(self, reader, directory, changes, runtime) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": 0,
            "output": "2 passed",
            "duration_ms": 12,
            "image": "test-image",
            "command": ["pytest", "-q"],
        }


def test_phase10_persisted_approval_and_explicit_github_publish(tmp_path: Path) -> None:
    database_url = os.environ.get("DATABASE_URL")
    assert database_url
    owner_uid = f"phase10-owner-{uuid.uuid4()}"
    other_uid = f"phase10-other-{uuid.uuid4()}"
    project_id = f"phase10-project-{uuid.uuid4()}"
    principal = FirebasePrincipal(uid=owner_uid, email=None, email_verified=True)
    key = Fernet.generate_key().decode()
    root = tmp_path / "repositories"
    project_root = root / "demo"
    project_root.mkdir(parents=True)
    source = project_root / "app.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    live_app = create_app(
        ApiSettings(
            database_url=database_url,
            agent_repositories_root=str(root),
            github_credentials_encryption_key=key,
        )
    )
    live_app.dependency_overrides[get_current_user] = lambda: principal
    live_app.dependency_overrides[get_owned_project] = lambda: {
        "name": "Demo",
        "repository_url": "https://github.com/octocat/demo",
    }
    sandbox = PassingSandbox()
    live_app.dependency_overrides[get_sandbox_service] = lambda: sandbox
    approval_ids: list[str] = []

    async def github_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/user"):
            return httpx.Response(
                200, json={"login": "octocat"}, headers={"X-OAuth-Scopes": "repo"}
            )
        if path.endswith("/repos/octocat/demo"):
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit"}})
        if path.endswith("/git/commits/base-commit"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if path.endswith("/git/trees/base-tree"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "app.py", "type": "blob", "sha": git_blob_sha(source.read_bytes())}
                    ]
                },
            )
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "new-blob"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit"})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/codex/approval"})
        if path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/octocat/demo/pull/22"})
        return httpx.Response(404, json={"message": "not found"})

    integration = GithubIntegration(key, transport=httpx.MockTransport(github_handler))
    live_app.dependency_overrides[github_api.get_github_integration] = lambda: integration
    live_app.dependency_overrides[approvals_api.get_github_integration] = lambda: integration

    async def verify() -> None:
        try:
            async with live_app.router.lifespan_context(live_app):
                transport = httpx.ASGITransport(app=live_app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    connected = await client.put(
                        "/api/v1/github/connection",
                        json={"token": "github-token-that-is-long-enough"},
                    )
                    assert connected.status_code == 200, connected.text
                    assert connected.json()["username"] == "octocat"
                    assert "github-token-that-is-long-enough" not in connected.text
                    encrypted = await live_app.state.database_service.get_github_connection(
                        owner_uid
                    )
                    assert encrypted is not None
                    assert "github-token-that-is-long-enough" not in encrypted["encrypted_token"]
                    assert (
                        integration.decrypt_token(encrypted["encrypted_token"])
                        == "github-token-that-is-long-enough"
                    )

                    change = {
                        "path": "app.py",
                        "content": "def value():\n    return 2\n",
                        "rationale": "Correct the returned value.",
                    }
                    created = await client.post(
                        f"/api/v1/projects/{project_id}/approvals",
                        json={
                            "repository_directory": "demo",
                            "title": "Correct the demo value",
                            "runtime": "python",
                            "changes": [change],
                        },
                    )
                    assert created.status_code == 201, created.text
                    approval = created.json()
                    approval_ids.append(approval["approval_id"])
                    assert approval["status"] == "pending"
                    assert "-    return 1" in approval["diff"]
                    assert approval["test_output"] == "2 passed"
                    assert source.read_text(encoding="utf-8") == "def value():\n    return 1\n"

                    sandbox.status = "failed"
                    failed_test = await client.post(
                        f"/api/v1/projects/{project_id}/approvals",
                        json={
                            "repository_directory": "demo",
                            "title": "Should not be approved",
                            "runtime": "python",
                            "changes": [change],
                        },
                    )
                    assert failed_test.status_code == 409
                    sandbox.status = "passed"

                    principal.uid = other_uid
                    private_read = await client.get(f"/api/v1/approvals/{approval['approval_id']}")
                    assert private_read.status_code == 404
                    private_list = await client.get(
                        "/api/v1/approvals", params={"project_id": project_id}
                    )
                    assert private_list.status_code == 200 and private_list.json()["items"] == []
                    principal.uid = owner_uid

                    published = await client.post(
                        f"/api/v1/approvals/{approval['approval_id']}/approve"
                    )
                    assert published.status_code == 200, published.text
                    assert published.json()["status"] == "published"
                    assert (
                        published.json()["pull_request_url"]
                        == "https://github.com/octocat/demo/pull/22"
                    )
                    assert [event["action"] for event in published.json()["events"]] == [
                        "created",
                        "approved",
                        "pull_request_created",
                    ]
                    repeated = await client.post(
                        f"/api/v1/approvals/{approval['approval_id']}/approve"
                    )
                    assert repeated.status_code == 409

                    rejected = await client.post(
                        f"/api/v1/projects/{project_id}/approvals",
                        json={
                            "repository_directory": "demo",
                            "title": "Change to reject",
                            "runtime": "python",
                            "changes": [change],
                        },
                    )
                    assert rejected.status_code == 201
                    approval_ids.append(rejected.json()["approval_id"])
                    rejected_result = await client.post(
                        f"/api/v1/approvals/{rejected.json()['approval_id']}/reject"
                    )
                    assert rejected_result.status_code == 200
                    assert rejected_result.json()["status"] == "rejected"

                    await live_app.state.database_service.delete_github_connection(owner_uid)
                    async with live_app.state.database_service.sessions.begin() as session:
                        await session.execute(
                            delete(ApprovalRecord).where(ApprovalRecord.owner_uid == owner_uid)
                        )
        finally:
            if live_app.state.database_service is None:
                await live_app.state.ollama_adapter.close()

    asyncio.run(verify())
