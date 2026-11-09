"""Ownership and persistence checks for a running Standard Firestore Emulator."""

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.core.auth import FirebasePrincipal, get_current_user
from app.core.firestore import get_firestore_client
from app.main import app

FIRESTORE_HOST = os.getenv("FIRESTORE_EMULATOR_HOST", "")
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")

pytestmark = pytest.mark.skipif(
    not FIRESTORE_HOST or not PROJECT_ID,
    reason="Set FIRESTORE_EMULATOR_HOST and FIREBASE_PROJECT_ID to run Firestore integration.",
)


def request_as(
    uid: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    principal = FirebasePrincipal(
        uid=uid,
        email=f"{uid}@example.test",
        email_verified=True,
    )
    app.dependency_overrides[get_current_user] = lambda: principal

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=body, headers=headers)

    try:
        return asyncio.run(request())
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_profiles_workspaces_projects_and_tenant_isolation(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    owner_uid = f"owner-{uuid.uuid4().hex}"
    other_uid = f"other-{uuid.uuid4().hex}"

    profile_body: dict[str, object] = {
        "full_name": "Project Owner",
        "developer_role": "developer",
        "experience_level": "intermediate",
        "preferred_languages": ["Python", "TypeScript"],
        "preferred_model": None,
        "development_environment": "Windows, VS Code",
        "github_username": "project-owner",
    }
    saved_profile = request_as(owner_uid, "PUT", "/api/v1/users/profile", profile_body)
    assert saved_profile.status_code == 200
    assert saved_profile.json()["uid"] == owner_uid
    assert saved_profile.json()["preferred_languages"] == ["Python", "TypeScript"]
    assert request_as(other_uid, "GET", "/api/v1/users/profile").status_code == 404

    workspace_body: dict[str, object] = {"name": "Main workspace", "description": "Owned data"}
    workspace_key = str(uuid.uuid4())
    workspace_headers = {"Idempotency-Key": workspace_key}
    owned_workspace = request_as(
        owner_uid, "POST", "/api/v1/workspaces", workspace_body, workspace_headers
    )
    replayed_workspace = request_as(
        owner_uid, "POST", "/api/v1/workspaces", workspace_body, workspace_headers
    )
    conflicting_workspace = request_as(
        owner_uid,
        "POST",
        "/api/v1/workspaces",
        {"name": "Different request", "description": "Key conflict"},
        workspace_headers,
    )
    second_workspace = request_as(
        owner_uid,
        "POST",
        "/api/v1/workspaces",
        {"name": "Second workspace", "description": "Pagination"},
        {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert owned_workspace.status_code == 201
    assert replayed_workspace.status_code == 201
    assert replayed_workspace.json()["id"] == owned_workspace.json()["id"]
    assert replayed_workspace.headers["idempotency-replayed"] == "true"
    assert conflicting_workspace.status_code == 409
    assert second_workspace.status_code == 201
    workspace_id = owned_workspace.json()["id"]

    first_page = request_as(owner_uid, "GET", "/api/v1/workspaces?page_size=1")
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["next_cursor"]
    next_page = request_as(
        owner_uid,
        "GET",
        f"/api/v1/workspaces?page_size=1&cursor={first_page.json()['next_cursor']}",
    )
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["next_cursor"] is None

    assert request_as(other_uid, "GET", f"/api/v1/workspaces/{workspace_id}").status_code == 404
    assert request_as(other_uid, "GET", "/api/v1/workspaces").json()["items"] == []

    workspace_key_hash = hashlib.sha256(
        f"{owner_uid}\0workspaces:create\0{workspace_key}".encode()
    ).hexdigest()
    get_firestore_client().collection("idempotency_keys").document(workspace_key_hash).update(
        {"expires_at": datetime.now(UTC) - timedelta(hours=1)}
    )
    expired_key_reuse = request_as(
        owner_uid, "POST", "/api/v1/workspaces", workspace_body, workspace_headers
    )
    assert expired_key_reuse.status_code == 201
    assert expired_key_reuse.json()["id"] != workspace_id
    assert expired_key_reuse.headers["idempotency-replayed"] == "false"

    project_body: dict[str, object] = {
        "workspace_id": workspace_id,
        "name": "Task Agent",
        "purpose": "Build a verified coding workflow.",
        "language": "Python",
        "framework": "FastAPI",
        "repository_url": "https://github.com/example/task-agent",
    }
    project_headers = {"Idempotency-Key": str(uuid.uuid4())}
    created_project = request_as(
        owner_uid, "POST", "/api/v1/projects", project_body, project_headers
    )
    assert created_project.status_code == 201
    replayed_project = request_as(
        owner_uid, "POST", "/api/v1/projects", project_body, project_headers
    )
    assert replayed_project.status_code == 201
    assert replayed_project.json()["id"] == created_project.json()["id"]
    assert replayed_project.headers["idempotency-replayed"] == "true"
    project_id = created_project.json()["id"]

    filtered_projects = request_as(
        owner_uid, "GET", f"/api/v1/projects?workspace_id={workspace_id}"
    )
    assert filtered_projects.status_code == 200
    assert [item["id"] for item in filtered_projects.json()["items"]] == [project_id]
    assert (
        request_as(other_uid, "GET", f"/api/v1/projects?workspace_id={workspace_id}").status_code
        == 404
    )

    assert request_as(other_uid, "GET", f"/api/v1/projects/{project_id}").status_code == 404
    assert request_as(other_uid, "DELETE", f"/api/v1/projects/{project_id}").status_code == 404
    assert (
        request_as(
            other_uid,
            "POST",
            "/api/v1/projects",
            project_body,
            {"Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    assert request_as(owner_uid, "GET", f"/api/v1/projects/{project_id}").status_code == 200
    assert request_as(owner_uid, "DELETE", f"/api/v1/projects/{project_id}").status_code == 204
    assert request_as(owner_uid, "GET", f"/api/v1/projects/{project_id}").status_code == 404
    assert (
        request_as(owner_uid, "POST", "/api/v1/projects", project_body, project_headers).status_code
        == 410
    )


def test_profile_and_project_input_validation() -> None:
    uid = f"validation-{uuid.uuid4().hex}"
    invalid_profile = request_as(
        uid,
        "PUT",
        "/api/v1/users/profile",
        {
            "full_name": "A",
            "developer_role": "admin",
            "experience_level": "expert",
            "preferred_languages": [],
        },
    )
    assert invalid_profile.status_code == 422

    invalid_repository = request_as(
        uid,
        "POST",
        "/api/v1/projects",
        {
            "workspace_id": "untrusted-workspace",
            "name": "Bad repository",
            "purpose": "Reject credentials embedded in a URL.",
            "repository_url": "https://username:password@example.test/repo",
        },
        {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert invalid_repository.status_code == 422
