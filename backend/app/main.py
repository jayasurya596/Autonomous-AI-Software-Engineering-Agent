"""Versioned FastAPI application with shared API protections."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.agents import router as agents_router
from app.api.approvals import router as approvals_router
from app.api.auth import router as auth_router
from app.api.executions import router as executions_router
from app.api.github import router as github_router
from app.api.memory import router as memory_router
from app.api.monitoring import router as monitoring_router
from app.api.projects import router as projects_router
from app.api.repositories import router as repositories_router
from app.api.users import router as users_router
from app.api.workspaces import router as workspaces_router
from app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)
from app.core.settings import ApiSettings
from app.persistence.database import DatabaseService
from app.services.agent_workflow import AgentWorkflow
from app.services.execution_manager import ExecutionManager
from app.services.memory import ProjectMemoryService
from app.services.ollama import OllamaModelAdapter
from app.services.r2_repositories import R2RepositoryStorage
from app.services.sandbox import SandboxService

if sys.platform == "win32":
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        # psycopg async I/O requires add_reader(), which ProactorEventLoop does not provide.
        asyncio.set_event_loop_policy(selector_policy())

HTTP_ERROR_MESSAGES = {
    400: ("bad_request", "The request could not be processed."),
    401: ("authentication_required", "A valid sign-in is required."),
    403: ("forbidden", "You are not allowed to perform this action."),
    404: ("not_found", "Resource not found."),
    409: ("conflict", "The request conflicts with the current resource state."),
    413: ("request_too_large", "Request body exceeds the configured size limit."),
    422: ("validation_error", "Request validation failed."),
    429: ("rate_limit_exceeded", "Too many requests. Try again later."),
    410: ("resource_gone", "The resource created by this request no longer exists."),
    502: ("model_error", "The local model returned an invalid structured response."),
    503: ("service_unavailable", "A required service is temporarily unavailable."),
}


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build the API app with explicit, testable runtime settings."""
    config = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(api_app: FastAPI) -> AsyncIterator[None]:
        stack = AsyncExitStack()
        manager: ExecutionManager | None = None
        try:
            if config.database_url:
                os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
                database = DatabaseService(config.database_url)
                await database.initialize()
                stack.push_async_callback(database.close)
                checkpoint_url = config.database_url.replace("+psycopg", "")
                checkpointer = await stack.enter_async_context(
                    AsyncPostgresSaver.from_conn_string(checkpoint_url)
                )
                await checkpointer.setup()
                workflow = AgentWorkflow(api_app.state.ollama_adapter, checkpointer=checkpointer)
                memory_service = ProjectMemoryService(database, api_app.state.ollama_adapter)
                manager = ExecutionManager(database, workflow, memory_service)
                await database.mark_interrupted()
                api_app.state.database_service = database
                api_app.state.checkpointer = checkpointer
                api_app.state.agent_workflow = workflow
                api_app.state.memory_service = memory_service
                api_app.state.execution_manager = manager
            yield
        finally:
            if manager is not None:
                await manager.shutdown()
            await stack.aclose()
            await api_app.state.ollama_adapter.close()

    api_app = FastAPI(
        title="Autonomous AI Software Engineering Agent",
        version="0.1.0",
        description="A verifiable, human-approved AI software engineering workflow.",
        debug=False,
        lifespan=lifespan,
    )
    api_app.state.settings = config
    api_app.state.ollama_adapter = OllamaModelAdapter(config)
    api_app.state.repository_storage = (
        R2RepositoryStorage(config) if config.r2_repository_storage_configured else None
    )
    api_app.state.agent_workflow = AgentWorkflow(api_app.state.ollama_adapter)
    api_app.state.sandbox_service = SandboxService(
        python_image=config.sandbox_python_image,
        node_image=config.sandbox_node_image,
        timeout_seconds=config.sandbox_timeout_seconds,
    )
    api_app.state.database_service = None
    api_app.state.checkpointer = None
    api_app.state.memory_service = None
    api_app.state.execution_manager = None
    api_app.include_router(agents_router, prefix="/api/v1")
    api_app.include_router(auth_router, prefix="/api/v1")
    api_app.include_router(users_router, prefix="/api/v1")
    api_app.include_router(workspaces_router, prefix="/api/v1")
    api_app.include_router(projects_router, prefix="/api/v1")
    api_app.include_router(repositories_router, prefix="/api/v1")
    api_app.include_router(executions_router, prefix="/api/v1")
    api_app.include_router(memory_router, prefix="/api/v1")
    api_app.include_router(approvals_router, prefix="/api/v1")
    api_app.include_router(github_router, prefix="/api/v1")
    api_app.include_router(monitoring_router, prefix="/api/v1")

    @api_app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = HTTP_ERROR_MESSAGES.get(
            exc.status_code, ("request_failed", "The request could not be completed.")
        )
        if exc.status_code in {409, 422} and isinstance(exc.detail, str):
            message = exc.detail[:500]
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
            headers=exc.headers,
        )

    @api_app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "location": list(error.get("loc", ())),
                "code": str(error.get("type", "invalid_value")),
                "message": "Invalid value.",
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": details,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    api_app.add_middleware(RequestSizeLimitMiddleware, max_bytes=config.max_request_body_bytes)
    api_app.add_middleware(
        RateLimitMiddleware,
        limit=config.api_rate_limit_requests,
        window_seconds=config.api_rate_limit_window_seconds,
    )
    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "Idempotency-Replayed",
        ],
        max_age=600,
    )
    api_app.add_middleware(RequestContextMiddleware)

    @api_app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Report basic process liveness without revealing service dependencies."""
        return {"status": "ok", "service": "software-engineering-agent-api"}

    return api_app


app = create_app()


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Report that the API process is ready."""
    return {"status": "ok", "service": "software-engineering-agent-api"}
