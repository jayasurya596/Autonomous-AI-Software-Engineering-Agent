"""Minimal public liveness endpoint for API monitoring."""

from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.persistence.database import DatabaseService
from app.services.ollama import OllamaModelAdapter, OllamaUnavailableError

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/health")
def monitoring_health() -> dict[str, str]:
    """Expose API liveness without disclosing dependency configuration."""
    return {"status": "ok", "service": "software-engineering-agent-api"}


@router.get("/ready", tags=["system"])
async def monitoring_readiness(request: Request) -> JSONResponse:
    """Report whether required runtime dependencies can serve agent requests."""
    checks: dict[str, str] = {"database": "not_configured", "model": "unavailable"}
    database = cast(DatabaseService | None, request.app.state.database_service)
    if database is not None:
        try:
            async with database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            checks["database"] = "ready"
        except Exception:
            checks["database"] = "unavailable"

    adapter = cast(OllamaModelAdapter, request.app.state.ollama_adapter)
    settings = request.app.state.settings
    if settings.ai_provider == "cloudflare":
        chat_model = adapter.model_name
        embedding_model = settings.cloudflare_embedding_model
    else:
        chat_model = settings.ollama_model
        embedding_model = settings.ollama_embedding_model
    try:
        models = set(await adapter.available_models())
        if chat_model in models and embedding_model in models:
            checks["model"] = "ready"
        else:
            checks["model"] = "missing"
    except OllamaUnavailableError:
        checks["model"] = "unavailable"

    ready = all(value == "ready" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
