"""Persistent background execution lifecycle, cancellation and bounded resume."""

import asyncio
import uuid

from pydantic import BaseModel

from app.agents.contracts import FailureContext
from app.persistence.database import DatabaseService
from app.services.agent_workflow import AgentWorkflow
from app.services.memory import ProjectMemoryService
from app.services.ollama import (
    InvalidModelOutputError,
    OllamaModelNotFoundError,
    OllamaUnavailableError,
)

MAX_RESUMES = 5


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


class ExecutionManager:
    """Start, cancel, recover and inspect tenant-scoped agent execution jobs."""

    def __init__(
        self,
        database: DatabaseService,
        workflow: AgentWorkflow,
        memory: ProjectMemoryService,
    ) -> None:
        self.database = database
        self.workflow = workflow
        self.memory = memory
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def create(self, owner_uid: str, project_id: str | None, goal: str) -> str:
        execution_id = str(uuid.uuid4())
        await self.database.create_execution(execution_id, owner_uid, project_id, goal)
        self._schedule(execution_id, owner_uid, project_id, goal, resume=False)
        return execution_id

    async def resume(self, owner_uid: str, execution_id: str) -> bool:
        record = await self.database.get_execution(owner_uid, execution_id)
        if record is None or record["status"] not in {"failed", "cancelled", "interrupted"}:
            return False
        if not await self.database.queue_resume(owner_uid, execution_id, MAX_RESUMES):
            return False
        self._schedule(
            execution_id,
            owner_uid,
            record["project_id"],
            record["goal"],
            resume=True,
        )
        return True

    async def cancel(self, owner_uid: str, execution_id: str) -> str | None:
        previous_status = await self.database.request_cancel(owner_uid, execution_id)
        if previous_status is None:
            return None
        if previous_status not in {"queued", "running"}:
            return previous_status
        task = self._tasks.get(execution_id)
        if task is None:
            await self.database.set_status(execution_id, "cancelled")
            return "cancelled"
        task.cancel()
        return "cancel_requested"

    def _schedule(
        self,
        execution_id: str,
        owner_uid: str,
        project_id: str | None,
        goal: str,
        *,
        resume: bool,
    ) -> None:
        task = asyncio.create_task(
            self._run(execution_id, owner_uid, project_id, goal, resume=resume),
            name=f"agent-execution-{execution_id}",
        )
        self._tasks[execution_id] = task

    async def _run(
        self,
        execution_id: str,
        owner_uid: str,
        project_id: str | None,
        goal: str,
        *,
        resume: bool,
    ) -> None:
        try:
            await self.database.set_status(execution_id, "running")
            if resume:
                result = await self.workflow.resume(execution_id)
            else:
                memory_context: list[dict[str, str]] = []
                if project_id:
                    items = await self.memory.search(owner_uid, project_id, goal)
                    memory_context = [
                        {"content": str(item["content"])}
                        for item in items
                        if isinstance(item.get("content"), str)
                    ]
                result = await self.workflow.run(
                    goal,
                    project_id,
                    execution_id=execution_id,
                    memory_context=memory_context,
                )
            await self.database.complete_execution(
                execution_id,
                _json_value(result),  # type: ignore[arg-type]
            )
        except asyncio.CancelledError:
            await self.database.set_status(execution_id, "cancelled")
        except Exception as exc:
            category, message = self._classify_failure(exc)
            debug_summary: str | None = None
            try:
                failure = FailureContext(
                    execution_id=execution_id,
                    failure_category=(
                        "validation" if isinstance(exc, InvalidModelOutputError) else "tool_error"
                    ),
                    summary=f"The agent execution encountered a {category} error.",
                )
                diagnosis, _ = await self.workflow.diagnose_failure(failure, project_id)
                debug_summary = diagnosis.root_cause_summary
            except Exception:
                debug_summary = None
            await self.database.fail_execution(
                execution_id,
                category=category,
                summary=message,
                debug_summary=debug_summary,
            )
        finally:
            self._tasks.pop(execution_id, None)

    @staticmethod
    def _classify_failure(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, OllamaModelNotFoundError):
            return "model", "The configured local model is not available."
        if isinstance(exc, OllamaUnavailableError):
            return "transient", "The local model service could not complete the request."
        if isinstance(exc, InvalidModelOutputError):
            return "model", "The local model repeatedly returned invalid structured output."
        if isinstance(exc, PermissionError):
            return "permission", "The execution was denied by a permission check."
        return "internal", "The execution stopped because of an internal agent error."

    async def shutdown(self) -> None:
        """Cancel local tasks on graceful API shutdown; durable checkpoints remain resumable."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
