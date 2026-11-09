"""Owner-scoped encrypted GitHub token management."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.auth import FirebasePrincipal, get_current_user
from app.persistence.database import DatabaseService
from app.schemas.phase10 import GithubConnectionResponse, GithubConnectRequest
from app.services.github import GithubIntegration, GithubIntegrationError

router = APIRouter(prefix="/github", tags=["github"])


def get_database(request: Request) -> DatabaseService | None:
    return cast(DatabaseService | None, request.app.state.database_service)


def require_database(database: DatabaseService | None) -> DatabaseService:
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Persistent GitHub connection storage is not configured.",
        )
    return database


def get_github_integration(request: Request) -> GithubIntegration:
    try:
        return GithubIntegration(request.app.state.settings.github_credentials_encryption_key)
    except GithubIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/connection", response_model=GithubConnectionResponse)
async def read_connection(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
) -> GithubConnectionResponse:
    record = await require_database(database).get_github_connection(principal.uid)
    if record is None:
        return GithubConnectionResponse(connected=False)
    return GithubConnectionResponse(
        connected=True,
        username=str(record["username"]),
        scopes=[scope for scope in str(record["scopes"]).split(",") if scope],
        connected_at=record["connected_at"],
    )


@router.put("/connection", response_model=GithubConnectionResponse)
async def connect_github(
    request_body: GithubConnectRequest,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
    integration: Annotated[GithubIntegration, Depends(get_github_integration)],
) -> GithubConnectionResponse:
    active_database = require_database(database)
    try:
        username, scopes = await integration.verify_token(request_body.token)
        await active_database.save_github_connection(
            principal.uid,
            username,
            integration.encrypt_token(request_body.token),
            scopes,
        )
    except GithubIntegrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = await active_database.get_github_connection(principal.uid)
    return GithubConnectionResponse(
        connected=True,
        username=username,
        scopes=[scope for scope in scopes.split(",") if scope],
        connected_at=record["connected_at"] if record else None,
    )


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_github(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    database: Annotated[DatabaseService | None, Depends(get_database)],
) -> None:
    await require_database(database).delete_github_connection(principal.uid)
