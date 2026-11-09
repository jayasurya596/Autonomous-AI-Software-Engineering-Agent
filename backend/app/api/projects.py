"""Project endpoints with workspace ownership enforcement."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from google.api_core.exceptions import GoogleAPICallError

from app.core.auth import FirebasePrincipal, get_current_user
from app.core.firestore import get_firestore_client
from app.repositories import firestore as repository
from app.schemas.project import ProjectInput, ProjectPage, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    project: ProjectInput,
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
) -> ProjectResponse:
    try:
        saved, replayed = repository.create_project(
            get_firestore_client(), principal.uid, project, idempotency_key
        )
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return ProjectResponse.model_validate(saved)


@router.get("", response_model=ProjectPage)
def list_projects(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    workspace_id: Annotated[str | None, Query(max_length=128)] = None,
) -> ProjectPage:
    try:
        items, next_cursor = repository.list_projects(
            get_firestore_client(), principal.uid, page_size, cursor, workspace_id
        )
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    return ProjectPage(
        items=[ProjectResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def read_project(
    project_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> ProjectResponse:
    try:
        project = repository.get_project(get_firestore_client(), principal.uid, project_id)
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project(
    project_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> Response:
    try:
        repository.delete_project(get_firestore_client(), principal.uid, project_id)
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
