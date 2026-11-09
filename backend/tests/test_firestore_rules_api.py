"""Exercise owner-only read and denied client-write Firestore rules."""

import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from app.core.firestore import get_firestore_client

FIRESTORE_HOST = os.getenv("FIRESTORE_EMULATOR_HOST", "")
AUTH_HOST = os.getenv("FIREBASE_AUTH_EMULATOR_HOST", "")
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
API_KEY = "demo-api-key"

pytestmark = pytest.mark.skipif(
    not FIRESTORE_HOST or not AUTH_HOST or not PROJECT_ID,
    reason="Set both Firebase emulator hosts and FIREBASE_PROJECT_ID to check rules.",
)


def _new_id_token(client: httpx.Client) -> tuple[str, str]:
    email = f"rules-{uuid.uuid4().hex}@example.test"
    response = client.post(
        f"/identitytoolkit.googleapis.com/v1/accounts:signUp?key={API_KEY}",
        json={
            "email": email,
            "password": "LocalTestPassword42!",
            "returnSecureToken": True,
        },
    )
    response.raise_for_status()
    return response.json()["localId"], response.json()["idToken"]


def test_owner_can_read_owned_document_but_cannot_write_it(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    auth_url = f"http://{AUTH_HOST.removeprefix('http://').removeprefix('https://')}"
    firestore_url = f"http://{FIRESTORE_HOST.removeprefix('http://').removeprefix('https://')}"
    with (
        httpx.Client(base_url=auth_url, timeout=10) as auth_client,
        httpx.Client(base_url=firestore_url, timeout=10) as firestore_client,
    ):
        owner_uid, owner_token = _new_id_token(auth_client)
        _, other_token = _new_id_token(auth_client)
        workspace_id = f"rules-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        database = get_firestore_client()
        reference = database.collection("workspaces").document(workspace_id)
        profile_reference = database.collection("users").document(owner_uid)
        project_reference = database.collection("projects").document(
            f"rules-project-{uuid.uuid4().hex}"
        )
        reference.set(
            {
                "owner_uid": owner_uid,
                "name": "Rules workspace",
                "description": "Seeded through the trusted Admin SDK.",
                "created_at": now,
                "updated_at": now,
            }
        )
        profile_reference.set(
            {
                "uid": owner_uid,
                "email": None,
                "full_name": "Rules Owner",
                "developer_role": "developer",
                "experience_level": "intermediate",
                "preferred_languages": ["Python", "TypeScript"],
                "created_at": now,
                "updated_at": now,
            }
        )
        project_reference.set(
            {
                "owner_uid": owner_uid,
                "workspace_id": workspace_id,
                "name": "Rules Project",
                "purpose": "Verify parent workspace ownership in rules.",
                "language": "Python",
                "created_at": now,
                "updated_at": now,
            }
        )

        workspace_path = (
            f"/v1/projects/{PROJECT_ID}/databases/(default)/documents/workspaces/{workspace_id}"
        )
        profile_path = f"/v1/projects/{PROJECT_ID}/databases/(default)/documents/users/{owner_uid}"
        project_path = (
            f"/v1/projects/{PROJECT_ID}/databases/(default)/documents/"
            f"projects/{project_reference.id}"
        )
        owner_read = firestore_client.get(
            workspace_path, headers={"Authorization": f"Bearer {owner_token}"}
        )
        other_read = firestore_client.get(
            workspace_path, headers={"Authorization": f"Bearer {other_token}"}
        )
        anonymous_read = firestore_client.get(workspace_path)
        owner_delete = firestore_client.delete(
            workspace_path, headers={"Authorization": f"Bearer {owner_token}"}
        )
        owner_profile_read = firestore_client.get(
            profile_path, headers={"Authorization": f"Bearer {owner_token}"}
        )
        owner_project_read = firestore_client.get(
            project_path, headers={"Authorization": f"Bearer {owner_token}"}
        )
        other_project_read = firestore_client.get(
            project_path, headers={"Authorization": f"Bearer {other_token}"}
        )

        assert owner_read.status_code == 200
        assert other_read.status_code == 403
        assert anonymous_read.status_code == 403
        assert owner_delete.status_code == 403
        assert owner_profile_read.status_code == 200
        assert owner_project_read.status_code == 200
        assert other_project_read.status_code == 403
        profile_reference.delete()
        project_reference.delete()
        reference.delete()
