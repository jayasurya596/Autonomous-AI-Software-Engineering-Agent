"""Async SQLAlchemy database connection and tenant-scoped persistence operations."""

import uuid
from datetime import datetime
from typing import Any, cast

from app.persistence.models import (
    AgentTraceRecord,
    ApprovalEventRecord,
    ApprovalRecord,
    Base,
    ExecutionRecord,
    GithubConnectionRecord,
    ProjectMemoryRecord,
)
from sqlalchemy import delete, desc, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload


class DatabaseService:
    """Own the SQLAlchemy engine and authenticated execution/memory persistence."""

    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def create_execution(
        self, execution_id: str, owner_uid: str, project_id: str | None, goal: str
    ) -> None:
        async with self.sessions.begin() as session:
            session.add(
                ExecutionRecord(
                    execution_id=execution_id,
                    owner_uid=owner_uid,
                    project_id=project_id,
                    goal=goal,
                    status="queued",
                )
            )

    async def get_execution(self, owner_uid: str, execution_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(ExecutionRecord).where(
                    ExecutionRecord.execution_id == execution_id,
                    ExecutionRecord.owner_uid == owner_uid,
                )
            )
            if record is None:
                return None
            return self._execution_view(record, include_result=True)

    async def list_executions(self, owner_uid: str, limit: int) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(ExecutionRecord)
                    .where(ExecutionRecord.owner_uid == owner_uid)
                    .order_by(desc(ExecutionRecord.created_at))
                    .limit(limit)
                )
            ).all()
            return [self._execution_view(record, include_result=False) for record in records]

    @staticmethod
    def _execution_view(record: ExecutionRecord, *, include_result: bool) -> dict[str, Any]:
        view: dict[str, Any] = {
            "execution_id": record.execution_id,
            "project_id": record.project_id,
            "goal": record.goal,
            "status": record.status,
            "cancel_requested": record.cancel_requested,
            "resume_count": record.resume_count,
            "failure_category": record.failure_category,
            "failure_summary": record.failure_summary,
            "debug_summary": record.debug_summary,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "traces": [
                {
                    "id": trace.id,
                    "agent_name": trace.agent_name,
                    "status": trace.status,
                    "summary": trace.summary,
                    "warnings": trace.warnings,
                    "evidence_count": trace.evidence_count,
                    "duration_ms": trace.duration_ms,
                    "created_at": trace.created_at,
                }
                for trace in record.traces
            ],
        }
        if include_result:
            view["result"] = record.result_json
        return view

    async def set_status(self, execution_id: str, status_value: str) -> None:
        async with self.sessions.begin() as session:
            values: dict[str, Any] = {
                "status": status_value,
                "updated_at": datetime.now().astimezone(),
            }
            if status_value == "cancelled":
                values["cancel_requested"] = False
            await session.execute(
                update(ExecutionRecord)
                .where(ExecutionRecord.execution_id == execution_id)
                .values(**values)
            )

    async def complete_execution(self, execution_id: str, result: dict[str, Any]) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(ExecutionRecord, execution_id)
            if record is None:
                return
            record.status = "completed"
            record.result_json = result
            record.failure_category = None
            record.failure_summary = None
            record.debug_summary = None
            record.cancel_requested = False
            record.updated_at = datetime.now().astimezone()
            await session.execute(
                delete(AgentTraceRecord).where(AgentTraceRecord.execution_id == execution_id)
            )
            for message in result.get("agent_messages", []):
                if not isinstance(message, dict):
                    continue
                invocation = message.get("invocation", {})
                output = message.get("result", {})
                if not isinstance(invocation, dict) or not isinstance(output, dict):
                    continue
                summary = output.get("summary")
                record.traces.append(
                    AgentTraceRecord(
                        agent_name=str(invocation.get("agent_name", "unknown")),
                        status=str(message.get("status", "completed")),
                        summary=summary[:500] if isinstance(summary, str) else None,
                        warnings=list(message.get("warnings", [])),
                        evidence_count=len(message.get("evidence", [])),
                    )
                )

    async def fail_execution(
        self,
        execution_id: str,
        *,
        category: str,
        summary: str,
        debug_summary: str | None,
    ) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(ExecutionRecord, execution_id)
            if record is None:
                return
            record.status = "failed"
            record.failure_category = category
            record.failure_summary = summary[:500]
            record.debug_summary = debug_summary[:600] if debug_summary else None
            record.updated_at = datetime.now().astimezone()

    async def request_cancel(self, owner_uid: str, execution_id: str) -> str | None:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(ExecutionRecord)
                .where(
                    ExecutionRecord.execution_id == execution_id,
                    ExecutionRecord.owner_uid == owner_uid,
                )
                .with_for_update()
            )
            if record is None:
                return None
            if record.status in {"queued", "running"}:
                record.cancel_requested = True
                record.updated_at = datetime.now().astimezone()
                return record.status
            return record.status

    async def queue_resume(self, owner_uid: str, execution_id: str, max_resumes: int) -> bool:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(ExecutionRecord)
                .where(
                    ExecutionRecord.execution_id == execution_id,
                    ExecutionRecord.owner_uid == owner_uid,
                )
                .with_for_update()
            )
            if (
                record is None
                or record.status not in {"failed", "cancelled", "interrupted"}
                or record.resume_count >= max_resumes
            ):
                return False
            record.resume_count += 1
            record.status = "queued"
            record.cancel_requested = False
            record.updated_at = datetime.now().astimezone()
            return True

    async def mark_interrupted(self) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(ExecutionRecord)
                .where(ExecutionRecord.status.in_(["queued", "running"]))
                .values(
                    status="interrupted",
                    failure_category="transient",
                    failure_summary="The API process stopped before the execution completed.",
                    updated_at=datetime.now().astimezone(),
                )
            )

    async def save_memory(
        self,
        owner_uid: str,
        project_id: str,
        content: str,
        embedding: list[float],
    ) -> uuid.UUID:
        async with self.sessions.begin() as session:
            record = ProjectMemoryRecord(
                owner_uid=owner_uid,
                project_id=project_id,
                content=content,
                embedding=embedding,
            )
            session.add(record)
            await session.flush()
            return record.id

    async def search_memories(
        self,
        owner_uid: str,
        project_id: str,
        embedding: list[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            distance = ProjectMemoryRecord.embedding.cosine_distance(embedding)
            rows = await session.execute(
                select(ProjectMemoryRecord, distance.label("cosine_distance"))
                .where(
                    ProjectMemoryRecord.owner_uid == owner_uid,
                    ProjectMemoryRecord.project_id == project_id,
                )
                .order_by(distance)
                .limit(limit)
            )
            typed_rows = cast(list[tuple[ProjectMemoryRecord, float]], rows.all())
            return [
                {
                    "id": record.id,
                    "content": record.content,
                    "created_at": record.created_at,
                    "similarity": 1.0 - float(cosine_distance),
                }
                for record, cosine_distance in typed_rows
            ]

    async def list_memories(self, owner_uid: str, project_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(ProjectMemoryRecord)
                    .where(
                        ProjectMemoryRecord.owner_uid == owner_uid,
                        ProjectMemoryRecord.project_id == project_id,
                    )
                    .order_by(desc(ProjectMemoryRecord.created_at))
                    .limit(100)
                )
            ).all()
            return [
                {"id": record.id, "content": record.content, "created_at": record.created_at}
                for record in records
            ]

    async def delete_memory(self, owner_uid: str, project_id: str, memory_id: uuid.UUID) -> bool:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(ProjectMemoryRecord).where(
                    ProjectMemoryRecord.id == memory_id,
                    ProjectMemoryRecord.owner_uid == owner_uid,
                    ProjectMemoryRecord.project_id == project_id,
                )
            )
            if record is None:
                return False
            await session.delete(record)
            return True

    async def delete_execution(self, owner_uid: str, execution_id: str) -> bool:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(ExecutionRecord).where(
                    ExecutionRecord.execution_id == execution_id,
                    ExecutionRecord.owner_uid == owner_uid,
                )
            )
            if record is None:
                return False
            await session.delete(record)
            return True

    async def save_github_connection(
        self, owner_uid: str, username: str, encrypted_token: str, scopes: str
    ) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(GithubConnectionRecord, owner_uid)
            if record is None:
                session.add(
                    GithubConnectionRecord(
                        owner_uid=owner_uid,
                        username=username,
                        encrypted_token=encrypted_token,
                        scopes=scopes[:1000],
                    )
                )
            else:
                record.username = username
                record.encrypted_token = encrypted_token
                record.scopes = scopes[:1000]
            await session.flush()

    async def get_github_connection(self, owner_uid: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            record = await session.get(GithubConnectionRecord, owner_uid)
            if record is None:
                return None
            return {
                "owner_uid": record.owner_uid,
                "username": record.username,
                "encrypted_token": record.encrypted_token,
                "scopes": record.scopes,
                "connected_at": record.connected_at,
            }

    async def delete_github_connection(self, owner_uid: str) -> bool:
        async with self.sessions.begin() as session:
            record = await session.get(GithubConnectionRecord, owner_uid)
            if record is None:
                return False
            await session.delete(record)
            return True

    async def create_approval(
        self,
        *,
        approval_id: str,
        owner_uid: str,
        project_id: str,
        repository_url: str,
        title: str,
        changes: list[dict[str, Any]],
        base_hashes: dict[str, str | None],
        diff_text: str,
        runtime: str,
        test_output: str,
        test_duration_ms: int,
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            record = ApprovalRecord(
                approval_id=approval_id,
                owner_uid=owner_uid,
                project_id=project_id,
                repository_url=repository_url,
                title=title,
                changes_json=changes,
                base_hashes=base_hashes,
                diff_text=diff_text,
                runtime=runtime,
                test_output=test_output[:20_000],
                test_duration_ms=test_duration_ms,
                status="pending",
            )
            record.events.append(
                ApprovalEventRecord(
                    actor_uid=owner_uid,
                    action="created",
                    summary="Sandbox tests passed; change set is ready for review.",
                )
            )
            session.add(record)
            await session.flush()
            return self._approval_view(record)

    async def list_approvals(
        self, owner_uid: str, project_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            query = select(ApprovalRecord).where(ApprovalRecord.owner_uid == owner_uid)
            if project_id is not None:
                query = query.where(ApprovalRecord.project_id == project_id)
            records = (
                await session.scalars(
                    query.order_by(desc(ApprovalRecord.created_at))
                    .limit(limit)
                    .options(selectinload(ApprovalRecord.events))
                )
            ).all()
            return [self._approval_view(record) for record in records]

    async def get_approval(self, owner_uid: str, approval_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.owner_uid == owner_uid,
                    ApprovalRecord.approval_id == approval_id,
                ).options(selectinload(ApprovalRecord.events))
            )
            return self._approval_view(record) if record is not None else None

    async def transition_approval(
        self,
        owner_uid: str,
        approval_id: str,
        *,
        allowed_statuses: set[str],
        new_status: str,
        action: str,
        summary: str,
        github_branch: str | None = None,
        pull_request_url: str | None = None,
        failure_summary: str | None = None,
    ) -> dict[str, Any] | None:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(ApprovalRecord)
                .where(
                    ApprovalRecord.owner_uid == owner_uid,
                    ApprovalRecord.approval_id == approval_id,
                )
                .options(selectinload(ApprovalRecord.events))
                .with_for_update()
            )
            if record is None or record.status not in allowed_statuses:
                return None
            record.status = new_status
            record.github_branch = github_branch
            record.pull_request_url = pull_request_url
            record.failure_summary = failure_summary[:500] if failure_summary else None
            record.updated_at = datetime.now().astimezone()
            record.events.append(
                ApprovalEventRecord(actor_uid=owner_uid, action=action, summary=summary[:500])
            )
            await session.flush()
            return self._approval_view(record)

    @staticmethod
    def _approval_view(record: ApprovalRecord) -> dict[str, Any]:
        return {
            "approval_id": record.approval_id,
            "project_id": record.project_id,
            "repository_url": record.repository_url,
            "title": record.title,
            "changes": record.changes_json,
            "base_hashes": record.base_hashes,
            "diff": record.diff_text,
            "runtime": record.runtime,
            "test_output": record.test_output,
            "test_duration_ms": record.test_duration_ms,
            "status": record.status,
            "github_branch": record.github_branch,
            "pull_request_url": record.pull_request_url,
            "failure_summary": record.failure_summary,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "events": [
                {
                    "actor_uid": event.actor_uid,
                    "action": event.action,
                    "summary": event.summary,
                    "created_at": event.created_at,
                }
                for event in record.events
            ],
        }
