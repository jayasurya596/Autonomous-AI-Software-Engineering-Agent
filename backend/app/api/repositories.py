"""Authenticated repository analysis and code-generation routes."""

import asyncio
import uuid
from pathlib import Path
from typing import Annotated, Any, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from google.api_core.exceptions import GoogleAPICallError

from app.core.auth import FirebasePrincipal, get_current_user
from app.core.firestore import get_firestore_client
from app.repositories import firestore as repository
from app.schemas.repository import (
    RepositoryAnalysisRequest,
    RepositoryAnalysisResponse,
    RepositoryChangeRequest,
    RepositoryChangeResponse,
    RepositoryTestRequest,
    RepositoryTestResponse,
)
from app.services.agent_workflow import AgentWorkflow
from app.services.ollama import (
    InvalidModelOutputError,
    OllamaUnavailableError,
)
from app.services.r2_repositories import (
    R2RepositoryStorage,
    RepositoryStorageError,
    RepositoryUploadError,
)
from app.services.repositories import RepositoryPathError, RepositoryReader
from app.services.sandbox import SandboxService, SandboxUnavailableError

router = APIRouter(prefix="/repositories", tags=["repositories"])


def get_agent_workflow(request: Request) -> AgentWorkflow:
    """Return the app-scoped agent workflow."""
    return cast(AgentWorkflow, request.app.state.agent_workflow)


def get_owned_project(
    project_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> dict[str, Any]:
    """Resolve a project only after enforcing its Firebase owner."""
    try:
        return repository.get_project(get_firestore_client(), principal.uid, project_id)
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc


def get_repository_reader(request: Request) -> RepositoryReader:
    settings = request.app.state.settings
    return RepositoryReader(settings.agent_repositories_root)


def get_repository_storage(request: Request) -> R2RepositoryStorage | None:
    """Return persistent source storage when the deployment uses Cloudflare R2."""
    return cast(R2RepositoryStorage | None, request.app.state.repository_storage)


async def resolve_repository_directory(
    request: Request,
    principal: FirebasePrincipal,
    project_id: str,
    requested_directory: str,
) -> str:
    """Resolve the owner's current R2 snapshot or retain the local development path."""
    storage = get_repository_storage(request)
    if storage is None:
        return requested_directory
    try:
        directory = await storage.current_directory(principal.uid, project_id)
        if directory is not None:
            repository_root = Path(
                request.app.state.settings.agent_repositories_root
            ).expanduser().resolve()
            directory = Path(directory).expanduser().resolve(strict=True).relative_to(
                repository_root
            ).as_posix()
    except RepositoryStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="The stored repository snapshot is outside the configured root.",
        ) from exc
    if directory is None:
        raise HTTPException(
            status_code=422,
            detail="Upload a ZIP source snapshot for this project before using repository tools.",
        )
    return directory


def get_sandbox_service(request: Request) -> SandboxService:
    """Return the app-scoped restricted test runner."""
    return cast(SandboxService, request.app.state.sandbox_service)


def project_summary(project: dict[str, Any]) -> str:
    language = str(project.get("language") or "Unknown")
    framework = str(project.get("framework") or "Not specified")
    return (
        f"{project.get('name', 'Project')}: {project.get('purpose', '')} "
        f"Language: {language}. Framework: {framework}."
    )[:1000]


def handle_repository_error(exc: Exception) -> NoReturn:
    if isinstance(exc, RepositoryPathError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The repository directory is unavailable or outside the configured root.",
        ) from exc
    if isinstance(exc, OllamaUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured local model is unavailable.",
        ) from exc
    if isinstance(exc, InvalidModelOutputError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The local model returned an invalid structured response.",
        ) from exc
    raise exc


@router.post("/projects/{project_id}/analyze", response_model=RepositoryAnalysisResponse)
async def analyze_project_repository(
    project_id: str,
    request_body: RepositoryAnalysisRequest,
    request: Request,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    project: Annotated[dict[str, Any], Depends(get_owned_project)],
    reader: Annotated[RepositoryReader, Depends(get_repository_reader)],
    workflow: Annotated[AgentWorkflow, Depends(get_agent_workflow)],
) -> RepositoryAnalysisResponse:
    """Analyze only a bounded, sanitized snapshot from the configured local root."""
    try:
        repository_directory = await resolve_repository_directory(
            request, principal, project_id, request_body.repository_directory
        )
        snapshot = reader.snapshot(repository_directory, project_summary(project))
        analysis, message = await workflow.analyze_repository(
            str(uuid.uuid4()), snapshot, project_id
        )
    except (RepositoryPathError, OllamaUnavailableError, InvalidModelOutputError) as exc:
        handle_repository_error(exc)
    return RepositoryAnalysisResponse(
        analysis=analysis,
        agent_message=message,
        snapshot_file_count=len(snapshot.files),
        omitted_file_count=snapshot.omitted_file_count,
    )


@router.post("/projects/{project_id}/changes", response_model=RepositoryChangeResponse)
async def propose_project_changes(
    project_id: str,
    request_body: RepositoryChangeRequest,
    request: Request,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    project: Annotated[dict[str, Any], Depends(get_owned_project)],
    reader: Annotated[RepositoryReader, Depends(get_repository_reader)],
    workflow: Annotated[AgentWorkflow, Depends(get_agent_workflow)],
) -> RepositoryChangeResponse:
    """Analyze a local repository and generate a preview-only change proposal."""
    try:
        repository_directory = await resolve_repository_directory(
            request, principal, project_id, request_body.repository_directory
        )
        snapshot = reader.snapshot(repository_directory, project_summary(project))
        execution_id = str(uuid.uuid4())
        analysis, _analysis_message = await workflow.analyze_repository(
            execution_id, snapshot, project_id
        )
        proposal, message = await workflow.generate_changes(
            execution_id, request_body.goal, snapshot, analysis, project_id
        )
    except (RepositoryPathError, OllamaUnavailableError, InvalidModelOutputError) as exc:
        handle_repository_error(exc)
    return RepositoryChangeResponse(
        analysis=analysis,
        proposal=proposal,
        agent_message=message,
        snapshot_file_count=len(snapshot.files),
        omitted_file_count=snapshot.omitted_file_count,
    )


@router.post("/projects/{project_id}/test", response_model=RepositoryTestResponse)
async def test_project_changes(
    project_id: str,
    request_body: RepositoryTestRequest,
    request: Request,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    _project: Annotated[dict[str, Any], Depends(get_owned_project)],
    reader: Annotated[RepositoryReader, Depends(get_repository_reader)],
    sandbox: Annotated[SandboxService, Depends(get_sandbox_service)],
) -> RepositoryTestResponse:
    """Test a proposal against a temporary copy using a fixed, offline test profile."""
    try:
        repository_directory = await resolve_repository_directory(
            request, principal, project_id, request_body.repository_directory
        )
        result = await asyncio.to_thread(
            sandbox.run_repository,
            reader,
            repository_directory,
            request_body.changes,
            request_body.runtime,
        )
    except RepositoryPathError as exc:
        handle_repository_error(exc)
    except SandboxUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The isolated test environment is not ready.",
        ) from exc
    return RepositoryTestResponse(runtime=request_body.runtime, **result)


@router.get("/projects/{project_id}/source")
async def read_project_source_status(
    project_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    _project: Annotated[dict[str, Any], Depends(get_owned_project)],
    storage: Annotated[R2RepositoryStorage | None, Depends(get_repository_storage)],
) -> dict[str, object]:
    """Report whether an owner has uploaded a persistent source snapshot."""
    if storage is None:
        raise HTTPException(status_code=404, detail="Persistent repository storage is not enabled.")
    try:
        return await storage.source_status(principal.uid, project_id)
    except RepositoryStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/projects/{project_id}/source")
async def upload_project_source(
    project_id: str,
    request: Request,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    _project: Annotated[dict[str, Any], Depends(get_owned_project)],
    storage: Annotated[R2RepositoryStorage | None, Depends(get_repository_storage)],
) -> dict[str, object]:
    """Validate and persist a ZIP source snapshot in the owner's R2 namespace."""
    if storage is None:
        raise HTTPException(
            status_code=503, detail="Persistent repository storage is not configured."
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/zip", "application/x-zip-compressed"}:
        raise HTTPException(status_code=415, detail="Upload the repository as a ZIP archive.")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit():
        if int(content_length) > request.app.state.settings.max_repository_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail="The repository ZIP exceeds the upload limit.",
            )
    archive_bytes = await request.body()
    if len(archive_bytes) > request.app.state.settings.max_repository_upload_bytes:
        raise HTTPException(status_code=413, detail="The repository ZIP exceeds the upload limit.")
    try:
        file_count = await storage.upload_zip(principal.uid, project_id, archive_bytes)
    except RepositoryUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"uploaded": True, "file_count": file_count}
