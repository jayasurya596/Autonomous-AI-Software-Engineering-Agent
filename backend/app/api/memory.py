"""Authenticated semantic project-memory API."""

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.repositories import get_owned_project
from app.core.auth import FirebasePrincipal, get_current_user
from app.schemas.executions import MemoryCreateRequest, ProjectMemory, ProjectMemoryListResponse
from app.services.memory import ProjectMemoryService
from app.services.ollama import InvalidModelOutputError, OllamaUnavailableError

router = APIRouter(prefix="/projects", tags=["project-memory"])


def get_memory_service(request: Request) -> ProjectMemoryService | None:
    return cast(ProjectMemoryService | None, request.app.state.memory_service)


def require_memory_service(service: ProjectMemoryService | None) -> ProjectMemoryService:
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project memory storage is not configured.",
        )
    return service


def map_memory_error(exc: Exception) -> None:
    if isinstance(exc, OllamaUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local embedding model is unavailable.",
        ) from exc
    if isinstance(exc, InvalidModelOutputError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The local embedding model returned an invalid vector.",
        ) from exc
    raise exc


@router.post("/{project_id}/memory", response_model=ProjectMemory, status_code=201)
async def create_project_memory(
    project_id: str,
    request_body: MemoryCreateRequest,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    _project: Annotated[dict[str, Any], Depends(get_owned_project)],
    service: Annotated[ProjectMemoryService | None, Depends(get_memory_service)],
) -> ProjectMemory:
    try:
        record = await require_memory_service(service).create(
            principal.uid, project_id, request_body.content
        )
    except (OllamaUnavailableError, InvalidModelOutputError) as exc:
        map_memory_error(exc)
    return ProjectMemory.model_validate(record)


@router.get("/{project_id}/memory", response_model=ProjectMemoryListResponse)
async def list_project_memory(
    project_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    _project: Annotated[dict[str, Any], Depends(get_owned_project)],
    service: Annotated[ProjectMemoryService | None, Depends(get_memory_service)],
    query: Annotated[str | None, Query(min_length=2, max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> ProjectMemoryListResponse:
    active_service = require_memory_service(service)
    try:
        if query:
            records = await active_service.search(principal.uid, project_id, query, limit)
        else:
            records = await active_service.list(principal.uid, project_id)
    except (OllamaUnavailableError, InvalidModelOutputError) as exc:
        map_memory_error(exc)
    return ProjectMemoryListResponse(
        items=[ProjectMemory.model_validate(record) for record in records]
    )


@router.delete("/{project_id}/memory/{memory_id}", status_code=204)
async def delete_project_memory(
    project_id: str,
    memory_id: UUID,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    _project: Annotated[dict[str, Any], Depends(get_owned_project)],
    service: Annotated[ProjectMemoryService | None, Depends(get_memory_service)],
) -> None:
    deleted = await require_memory_service(service).delete(principal.uid, project_id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project memory was not found.")
