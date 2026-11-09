"""Tenant-scoped Firestore persistence operations."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import HTTPException, status
from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud.firestore_v1.field_path import FieldPath

from app.schemas.profile import UserProfileInput
from app.schemas.project import ProjectInput
from app.schemas.workspace import WorkspaceInput

PAGE_MAX = 100
IDEMPOTENCY_RETENTION = timedelta(hours=24)


def _document_data(snapshot: Any) -> dict[str, Any]:
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def _owned_document(db: Any, collection: str, document_id: str, owner_uid: str) -> Any:
    snapshot = db.collection(collection).document(document_id).get()
    data = snapshot.to_dict() or {}
    if not snapshot.exists or data.get("owner_uid") != owner_uid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    return snapshot


def _list_owned(
    db: Any,
    collection_name: str,
    owner_uid: str,
    page_size: int,
    cursor: str | None = None,
    workspace_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    collection = db.collection(collection_name)
    query = collection.where(filter=FieldFilter("owner_uid", "==", owner_uid))
    if workspace_id is not None:
        query = query.where(filter=FieldFilter("workspace_id", "==", workspace_id))
    query = query.order_by(FieldPath.document_id()).limit(page_size + 1)

    if cursor:
        cursor_snapshot = collection.document(cursor).get()
        cursor_data = cursor_snapshot.to_dict() or {}
        if not cursor_snapshot.exists or cursor_data.get("owner_uid") != owner_uid:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
        query = query.start_after(cursor_snapshot)

    snapshots = list(query.stream())
    has_next = len(snapshots) > page_size
    page = snapshots[:page_size]
    items = [_document_data(snapshot) for snapshot in page]
    next_cursor = page[-1].id if has_next and page else None
    return items, next_cursor


def get_profile(db: Any, uid: str) -> dict[str, Any] | None:
    snapshot = db.collection("users").document(uid).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["uid"] = uid
    return data


def save_profile(db: Any, uid: str, email: str | None, profile: UserProfileInput) -> dict[str, Any]:
    reference = db.collection("users").document(uid)
    previous = reference.get()
    data = profile.model_dump()
    data.update(
        {
            "uid": uid,
            "email": email,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )
    if not previous.exists:
        data["created_at"] = firestore.SERVER_TIMESTAMP
    reference.set(data, merge=True)
    return get_profile(db, uid) or {}


def _idempotent_create(
    db: Any,
    owner_uid: str,
    operation: str,
    idempotency_key: str,
    collection_name: str,
    input_data: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Create once for a user/key and replay the existing resource for retries."""
    serialized_input = json.dumps(input_data, sort_keys=True, separators=(",", ":"))
    body_hash = hashlib.sha256(serialized_input.encode("utf-8")).hexdigest()
    key_hash = hashlib.sha256(f"{owner_uid}\0{operation}\0{idempotency_key}".encode()).hexdigest()
    idempotency_ref = db.collection("idempotency_keys").document(key_hash)
    resource_ref = db.collection(collection_name).document()
    now = datetime.now(UTC)
    resource_data = {
        **input_data,
        "owner_uid": owner_uid,
        "created_at": now,
        "updated_at": now,
    }

    @firestore.transactional  # type: ignore[untyped-decorator]
    def create_or_replay(transaction: Any) -> tuple[dict[str, Any], bool]:
        previous = idempotency_ref.get(transaction=transaction)
        if previous.exists:
            record = previous.to_dict() or {}
            expires_at = record.get("expires_at")
            if isinstance(expires_at, datetime) and expires_at > now:
                if record.get("body_hash") != body_hash:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key was already used for a different request.",
                    )
                if (
                    record.get("owner_uid") != owner_uid
                    or record.get("operation") != operation
                    or record.get("resource_collection") != collection_name
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key conflicts with a previous request.",
                    )
                existing_ref = db.collection(collection_name).document(
                    str(record.get("resource_id", ""))
                )
                existing = existing_ref.get(transaction=transaction)
                if not existing.exists:
                    raise HTTPException(
                        status_code=status.HTTP_410_GONE,
                        detail="The resource created by this idempotency key no longer exists.",
                    )
                return _document_data(existing), True

        transaction.create(resource_ref, resource_data)
        transaction.set(
            idempotency_ref,
            {
                "owner_uid": owner_uid,
                "operation": operation,
                "body_hash": body_hash,
                "resource_collection": collection_name,
                "resource_id": resource_ref.id,
                "expires_at": now + IDEMPOTENCY_RETENTION,
            },
        )
        result = {**resource_data, "id": resource_ref.id}
        return result, False

    return cast(tuple[dict[str, Any], bool], create_or_replay(db.transaction()))


def create_workspace(
    db: Any, owner_uid: str, workspace: WorkspaceInput, idempotency_key: str
) -> tuple[dict[str, Any], bool]:
    return _idempotent_create(
        db,
        owner_uid,
        "workspaces:create",
        idempotency_key,
        "workspaces",
        workspace.model_dump(mode="json"),
    )


def list_workspaces(
    db: Any, owner_uid: str, page_size: int, cursor: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    return _list_owned(db, "workspaces", owner_uid, page_size, cursor)


def get_workspace(db: Any, owner_uid: str, workspace_id: str) -> dict[str, Any]:
    return _document_data(_owned_document(db, "workspaces", workspace_id, owner_uid))


def create_project(
    db: Any, owner_uid: str, project: ProjectInput, idempotency_key: str
) -> tuple[dict[str, Any], bool]:
    _owned_document(db, "workspaces", project.workspace_id, owner_uid)
    return _idempotent_create(
        db,
        owner_uid,
        "projects:create",
        idempotency_key,
        "projects",
        project.model_dump(mode="json"),
    )


def list_projects(
    db: Any,
    owner_uid: str,
    page_size: int,
    cursor: str | None = None,
    workspace_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    if workspace_id is not None:
        _owned_document(db, "workspaces", workspace_id, owner_uid)
    return _list_owned(db, "projects", owner_uid, page_size, cursor, workspace_id)


def get_project(db: Any, owner_uid: str, project_id: str) -> dict[str, Any]:
    return _document_data(_owned_document(db, "projects", project_id, owner_uid))


def delete_project(db: Any, owner_uid: str, project_id: str) -> None:
    snapshot = _owned_document(db, "projects", project_id, owner_uid)
    snapshot.reference.delete()
