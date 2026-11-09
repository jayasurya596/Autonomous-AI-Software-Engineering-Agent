"""Tenant-scoped persistent execution API."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from google.api_core.exceptions import GoogleAPICallError

from app.core.auth import FirebasePrincipal, get_current_user
from app.core.firestore import get_firestore_client
from app.repositories import firestore as firestore_repository
from app.schemas.executions import (
    ExecutionCreateRequest,
    ExecutionListResponse,
    ExecutionResponse,
)
from app.services.execution_manager import ExecutionManager

router = APIRouter(prefix="/executions", tags=["executions"])


def get_execution_manager(request: Request) -> ExecutionManager | None:
    return cast(ExecutionManager | None, request.app.state.execution_manager)


def require_manager(manager: ExecutionManager | None) -> ExecutionManager:
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Persistent execution storage is not configured.",
        )
    return manager


@router.post("", response_model=ExecutionResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_execution(
    request_body: ExecutionCreateRequest,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    manager: Annotated[ExecutionManager | None, Depends(get_execution_manager)],
) -> ExecutionResponse:
    """Queue a durable, resumable execution owned by the authenticated user."""
    if request_body.project_id:
        try:
            firestore_repository.get_project(
                get_firestore_client(), principal.uid, request_body.project_id
            )
        except GoogleAPICallError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Firestore is temporarily unavailable.",
            ) from exc
    active_manager = require_manager(manager)
    execution_id = await active_manager.create(
        principal.uid, request_body.project_id, request_body.goal
    )
    result = await active_manager.database.get_execution(principal.uid, execution_id)
    if result is None:
        raise HTTPException(status_code=500, detail="The execution could not be created.")
    return ExecutionResponse.model_validate(result)


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    manager: Annotated[ExecutionManager | None, Depends(get_execution_manager)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ExecutionListResponse:
    result = await require_manager(manager).database.list_executions(principal.uid, limit)
    return ExecutionListResponse(items=[ExecutionResponse.model_validate(item) for item in result])


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def read_execution(
    execution_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    manager: Annotated[ExecutionManager | None, Depends(get_execution_manager)],
) -> ExecutionResponse:
    result = await require_manager(manager).database.get_execution(principal.uid, execution_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    return ExecutionResponse.model_validate(result)


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    manager: Annotated[ExecutionManager | None, Depends(get_execution_manager)],
) -> dict[str, str]:
    result = await require_manager(manager).cancel(principal.uid, execution_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    return {"status": result}


@router.post("/{execution_id}/resume", response_model=ExecutionResponse)
async def resume_execution(
    execution_id: str,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    manager: Annotated[ExecutionManager | None, Depends(get_execution_manager)],
) -> ExecutionResponse:
    active_manager = require_manager(manager)
    if not await active_manager.resume(principal.uid, execution_id):
        record = await active_manager.database.get_execution(principal.uid, execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Execution not found.")
        raise HTTPException(
            status_code=409,
            detail="Execution is not resumable or has reached its retry limit.",
        )
    record = await active_manager.database.get_execution(principal.uid, execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    return ExecutionResponse.model_validate(record)
