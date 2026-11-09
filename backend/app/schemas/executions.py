"""Persistent execution, trace and project-memory API models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExecutionStatus = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]


class ExecutionCreateRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=4000)
    project_id: str | None = Field(default=None, max_length=128)


class ExecutionTrace(BaseModel):
    id: int
    agent_name: str
    status: str
    summary: str | None
    warnings: list[str]
    evidence_count: int
    duration_ms: int | None
    created_at: datetime


class ExecutionResponse(BaseModel):
    execution_id: str
    project_id: str | None
    goal: str
    status: ExecutionStatus
    cancel_requested: bool
    resume_count: int
    failure_category: str | None
    failure_summary: str | None
    debug_summary: str | None
    created_at: datetime
    updated_at: datetime
    traces: list[ExecutionTrace] = Field(default_factory=list)
    result: dict[str, object] | None = None


class ExecutionListResponse(BaseModel):
    items: list[ExecutionResponse]


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=10, max_length=4000)


class ProjectMemory(BaseModel):
    id: str
    content: str
    created_at: datetime
    similarity: float | None = None


class ProjectMemoryListResponse(BaseModel):
    items: list[ProjectMemory]
