"""Authenticated local-model health and agent-run endpoints."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.auth import FirebasePrincipal, get_current_user
from app.schemas.agents import (
    AgentRunRequest,
    AgentRunResponse,
    OllamaHealthResponse,
)
from app.services.agent_workflow import AgentWorkflow
from app.services.ollama import (
    InvalidModelOutputError,
    OllamaModelAdapter,
    OllamaUnavailableError,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def get_ollama_adapter(request: Request) -> OllamaModelAdapter:
    """Return the app-scoped local model adapter."""
    return cast(OllamaModelAdapter, request.app.state.ollama_adapter)


def get_agent_workflow(request: Request) -> AgentWorkflow:
    """Return the app-scoped compiled graph."""
    return cast(AgentWorkflow, request.app.state.agent_workflow)


@router.get("/health", response_model=OllamaHealthResponse)
async def ollama_health(
    _principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    adapter: Annotated[OllamaModelAdapter, Depends(get_ollama_adapter)],
) -> OllamaHealthResponse:
    """Check whether Ollama and the configured model are ready."""
    return await adapter.health_check()


@router.post("/runs", response_model=AgentRunResponse)
async def create_agent_run(
    request_body: AgentRunRequest,
    _principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
    workflow: Annotated[AgentWorkflow, Depends(get_agent_workflow)],
) -> AgentRunResponse:
    """Analyze a goal, build a plan and return a non-executing coding brief."""
    try:
        result = await workflow.run(request_body.goal)
    except OllamaUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured local model is unavailable.",
        ) from exc
    except InvalidModelOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The local model returned an invalid structured response.",
        ) from exc

    return AgentRunResponse(
        status="completed",
        model=workflow.model.model_name,
        **result,
    )
