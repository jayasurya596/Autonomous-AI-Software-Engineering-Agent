"""Opt-in PostgreSQL, checkpoint, cancellation, resume and semantic-memory integration."""

import asyncio
import os
import uuid

import httpx
import pytest
from app.api.auth import get_current_user
from app.api.repositories import get_owned_project
from app.core.auth import FirebasePrincipal
from app.core.settings import ApiSettings
from app.main import create_app
from app.services.agent_workflow import AgentWorkflow
from test_agent_workflow import FakeModel

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_LIVE_TESTS") != "1",
    reason="Set RUN_POSTGRES_LIVE_TESTS=1 and DATABASE_URL for PostgreSQL integration.",
)


class SlowOnceModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.block_first_call = True

    async def generate_structured(self, system_prompt, user_prompt, response_model):
        if self.block_first_call:
            self.block_first_call = False
            self.started.set()
            await asyncio.Event().wait()
        return await super().generate_structured(system_prompt, user_prompt, response_model)


def test_postgres_execution_checkpoint_resume_and_memory_are_tenant_scoped() -> None:
    database_url = os.environ.get("DATABASE_URL")
    assert database_url, "DATABASE_URL is required for this opt-in test."
    owner_uid = f"phase9-owner-{uuid.uuid4()}"
    other_uid = f"phase9-other-{uuid.uuid4()}"
    project_id = f"phase9-project-{uuid.uuid4()}"
    principal = FirebasePrincipal(uid=owner_uid, email=None, email_verified=True)
    live_app = create_app(ApiSettings(database_url=database_url))
    live_app.dependency_overrides[get_current_user] = lambda: principal
    live_app.dependency_overrides[get_owned_project] = lambda: {"name": "Memory test project"}

    async def verify() -> None:
        execution_id: str | None = None
        memory_id: str | None = None
        project_execution_id: str | None = None
        slow_model = SlowOnceModel()
        try:
            async with live_app.router.lifespan_context(live_app):
                workflow = AgentWorkflow(slow_model, checkpointer=live_app.state.checkpointer)
                live_app.state.execution_manager.workflow = workflow
                transport = httpx.ASGITransport(app=live_app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    created = await client.post(
                        "/api/v1/executions",
                        json={"goal": "Create a persistent workflow execution."},
                    )
                    assert created.status_code == 202, created.text
                    execution_id = created.json()["execution_id"]
                    await asyncio.wait_for(slow_model.started.wait(), timeout=10)

                    cancelled = await client.post(f"/api/v1/executions/{execution_id}/cancel")
                    assert cancelled.status_code == 200
                    assert cancelled.json()["status"] == "cancel_requested"
                    for _ in range(100):
                        state = await client.get(f"/api/v1/executions/{execution_id}")
                        if state.json()["status"] == "cancelled":
                            break
                        await asyncio.sleep(0.05)
                    assert state.json()["status"] == "cancelled"

                    resumed = await client.post(f"/api/v1/executions/{execution_id}/resume")
                    assert resumed.status_code == 200, resumed.text
                    for _ in range(300):
                        state = await client.get(f"/api/v1/executions/{execution_id}")
                        if state.json()["status"] in {"completed", "failed"}:
                            break
                        await asyncio.sleep(0.05)
                    result = state.json()
                    assert result["status"] == "completed", result
                    assert len(result["traces"]) == 7
                    assert result["resume_count"] == 1

                    checkpoint_config = {"configurable": {"thread_id": execution_id}}
                    checkpoints = [
                        item async for item in live_app.state.checkpointer.alist(checkpoint_config)
                    ]
                    assert checkpoints

                    principal.uid = owner_uid
                    created_memory = await client.post(
                        f"/api/v1/projects/{project_id}/memory",
                        json={"content": "This project uses asynchronous FastAPI endpoints."},
                    )
                    assert created_memory.status_code == 201, created_memory.text
                    memory_id = created_memory.json()["id"]
                    matches = await client.get(
                        f"/api/v1/projects/{project_id}/memory",
                        params={"query": "This project uses asynchronous FastAPI endpoints."},
                    )
                    assert matches.status_code == 200, matches.text
                    assert matches.json()["items"][0]["id"] == memory_id
                    assert matches.json()["items"][0]["similarity"] > 0.9

                    model = FakeModel()
                    live_app.state.execution_manager.workflow = AgentWorkflow(
                        model, checkpointer=live_app.state.checkpointer
                    )
                    project_execution_id = await live_app.state.execution_manager.create(
                        owner_uid,
                        project_id,
                        "Use the saved project conventions when planning this task.",
                    )
                    for _ in range(300):
                        project_state = await live_app.state.database_service.get_execution(
                            owner_uid, project_execution_id
                        )
                        if project_state and project_state["status"] in {"completed", "failed"}:
                            break
                        await asyncio.sleep(0.05)
                    assert project_state is not None and project_state["status"] == "completed"
                    assert "asynchronous FastAPI endpoints" in model.prompts[0][1]

                    principal.uid = other_uid
                    hidden_execution = await client.get(f"/api/v1/executions/{execution_id}")
                    assert hidden_execution.status_code == 404
                    other_memory = await client.get(f"/api/v1/projects/{project_id}/memory")
                    assert other_memory.status_code == 200
                    assert other_memory.json()["items"] == []
                    principal.uid = owner_uid

                    deleted = await client.delete(
                        f"/api/v1/projects/{project_id}/memory/{memory_id}"
                    )
                    assert deleted.status_code == 204
                    if project_execution_id:
                        await live_app.state.database_service.delete_execution(
                            owner_uid, project_execution_id
                        )
                        await live_app.state.checkpointer.adelete_thread(project_execution_id)
                        project_execution_id = None
                    if execution_id:
                        await live_app.state.database_service.delete_execution(
                            owner_uid, execution_id
                        )
                        await live_app.state.checkpointer.adelete_thread(execution_id)
                        execution_id = None
        finally:
            if live_app.state.database_service is None:
                await live_app.state.ollama_adapter.close()

    asyncio.run(verify())
