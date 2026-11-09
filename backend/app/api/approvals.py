"""Human review queue for sandbox-tested changes and gated GitHub pull requests."""

import asyncio
import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.github import get_database, require_database
from app.api.repositories import (
    get_owned_project,
    get_repository_reader,
    get_sandbox_service,
    resolve_repository_directory,
)
from app.core.auth import FirebasePrincipal, get_current_user
from app.persistence.database import DatabaseService
from app.schemas.phase10 import (
    ApprovalCreateRequest,
    ApprovalListResponse,
    ApprovalResponse,
)
from app.services.github import (
    GithubIntegration,
    GithubIntegrationError,
    GithubStaleRepositoryError,
    parse_github_repository,
)
from app.services.repositories import RepositoryPathError, RepositoryReader
from app.services.sandbox import SandboxService, SandboxUnavailableError

router = APIRouter(tags=["approvals"])


def get_github_integration(request: Request) -> GithubIntegration:
    try:
        return GithubIntegration(request.app.state.settings.github_credentials_encryption_key)
    except GithubIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/approvals",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_approval(
    project_id: str,
    request_body: ApprovalCreateRequest,
    request: Request,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    project: Annotated[dict[str, object], Depends(get_owned_project)],
    reader: Annotated[RepositoryReader, Depends(get_repository_reader)],
    sandbox: Annotated[SandboxService, Depends(get_sandbox_service)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
) -> ApprovalResponse:
    active_database = require_database(database)
    repository_url = project.get("repository_url")
    if not isinstance(repository_url, str):
        raise HTTPException(
            status_code=422,
            detail="Add a GitHub repository URL to this project first.",
        )

    try:
        parse_github_repository(repository_url)
        repository_directory = await resolve_repository_directory(
            request, principal, project_id, request_body.repository_directory
        )
        base_hashes, diff_text = reader.capture_change_bases(
            repository_directory, request_body.changes
        )
        result = await asyncio.to_thread(
            sandbox.run_repository,
            reader,
            repository_directory,
            request_body.changes,
            request_body.runtime,
        )
    except GithubIntegrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryPathError as exc:
        raise HTTPException(
            status_code=422,
            detail="The repository or one of the changed files is unavailable or unsafe.",
        ) from exc
    except SandboxUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="The isolated test environment is not ready."
        ) from exc
    if result["status"] != "passed":
        raise HTTPException(
            status_code=409,
            detail=(
                "The change set must pass sandbox tests before review "
                f"(status: {result['status']})."
            ),
        )
    record = await active_database.create_approval(
        approval_id=str(uuid.uuid4()),
        owner_uid=principal.uid,
        project_id=project_id,
        repository_url=repository_url,
        title=request_body.title,
        changes=[change.model_dump(mode="json") for change in request_body.changes],
        base_hashes=base_hashes,
        diff_text=diff_text,
        runtime=request_body.runtime,
        test_output=result["output"],
        test_duration_ms=result["duration_ms"],
    )
    return ApprovalResponse.model_validate(record)


@router.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
    project_id: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApprovalListResponse:
    records = await require_database(database).list_approvals(principal.uid, project_id, limit)
    return ApprovalListResponse(
        items=[ApprovalResponse.model_validate(record) for record in records]
    )


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
async def read_approval(
    approval_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
) -> ApprovalResponse:
    record = await require_database(database).get_approval(principal.uid, approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    return ApprovalResponse.model_validate(record)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    approval_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
) -> ApprovalResponse:
    record = await require_database(database).transition_approval(
        principal.uid,
        approval_id,
        allowed_statuses={"pending"},
        new_status="rejected",
        action="rejected",
        summary="The reviewer rejected this change set.",
    )
    if record is None:
        raise HTTPException(status_code=409, detail="This approval is no longer awaiting review.")
    return ApprovalResponse.model_validate(record)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_and_publish(
    approval_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
    integration: Annotated[GithubIntegration, Depends(get_github_integration)],
) -> ApprovalResponse:
    active_database = require_database(database)
    pending = await active_database.get_approval(principal.uid, approval_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if pending["status"] != "pending":
        raise HTTPException(status_code=409, detail="This approval is no longer awaiting review.")
    connection = await active_database.get_github_connection(principal.uid)
    if connection is None:
        raise HTTPException(
            status_code=409,
            detail="Connect a GitHub account before approving this change.",
        )
    try:
        token = integration.decrypt_token(str(connection["encrypted_token"]))
    except GithubIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    publishing = await active_database.transition_approval(
        principal.uid,
        approval_id,
        allowed_statuses={"pending"},
        new_status="publishing",
        action="approved",
        summary="The reviewer approved publishing a pull request to GitHub.",
    )
    if publishing is None:
        raise HTTPException(status_code=409, detail="This approval is no longer awaiting review.")
    try:
        branch, pull_request_url = await integration.publish_pull_request(
            token,
            str(pending["repository_url"]),
            approval_id,
            str(pending["title"]),
            cast(list[dict[str, object]], pending["changes"]),
            cast(dict[str, str | None], pending.get("base_hashes", {})),
        )
    except GithubIntegrationError as exc:
        summary = str(exc)[:500]
        await active_database.transition_approval(
            principal.uid,
            approval_id,
            allowed_statuses={"publishing"},
            new_status="failed",
            action="publish_failed",
            summary=summary,
            failure_summary=summary,
        )
        code = 409 if isinstance(exc, GithubStaleRepositoryError) else 502
        raise HTTPException(status_code=code, detail=summary) from exc

    record = await active_database.transition_approval(
        principal.uid,
        approval_id,
        allowed_statuses={"publishing"},
        new_status="published",
        action="pull_request_created",
        summary="GitHub pull request created successfully.",
        github_branch=branch,
        pull_request_url=pull_request_url,
    )
    if record is None:
        raise HTTPException(
            status_code=500,
            detail="The pull request was created but its status could not be saved.",
        )
    return ApprovalResponse.model_validate(record)
