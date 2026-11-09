"""Workspace endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from google.api_core.exceptions import GoogleAPICallError

from app.core.auth import FirebasePrincipal, get_current_user
from app.core.firestore import get_firestore_client
from app.repositories import firestore as repository
from app.schemas.workspace import WorkspaceInput, WorkspacePage, WorkspaceResponse

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(
    workspace: WorkspaceInput,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=16,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
) -> WorkspaceResponse:
    try:
        saved, replayed = repository.create_workspace(
            get_firestore_client(), principal.uid, workspace, idempotency_key
        )
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return WorkspaceResponse.model_validate(saved)


@router.get("", response_model=WorkspacePage)
def list_workspaces(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
) -> WorkspacePage:
    try:
        items, next_cursor = repository.list_workspaces(
            get_firestore_client(), principal.uid, page_size, cursor
        )
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    return WorkspacePage(
        items=[WorkspaceResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def read_workspace(
    workspace_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> WorkspaceResponse:
    try:
        workspace = repository.get_workspace(get_firestore_client(), principal.uid, workspace_id)
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    return WorkspaceResponse.model_validate(workspace)
