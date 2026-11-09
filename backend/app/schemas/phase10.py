"""Contracts for human review, GitHub connection and pull request workflows."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.agents.contracts import FileChangeProposal


class GithubConnectRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    token: str = Field(min_length=20, max_length=512, repr=False)


class GithubConnectionResponse(BaseModel):
    connected: bool
    username: str | None = None
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    repository_directory: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=5, max_length=300)
    runtime: Literal["python", "node"]
    changes: list[FileChangeProposal] = Field(min_length=1, max_length=5)


class ApprovalEvent(BaseModel):
    actor_uid: str
    action: str
    summary: str
    created_at: datetime


class ApprovalResponse(BaseModel):
    approval_id: str
    project_id: str
    repository_url: HttpUrl
    title: str
    changes: list[FileChangeProposal]
    diff: str = Field(max_length=100_000)
    runtime: Literal["python", "node"]
    test_output: str = Field(max_length=20_000)
    test_duration_ms: int = Field(ge=0)
    status: Literal["pending", "publishing", "published", "rejected", "failed"]
    github_branch: str | None = None
    pull_request_url: HttpUrl | None = None
    failure_summary: str | None = None
    created_at: datetime
    updated_at: datetime
    events: list[ApprovalEvent] = Field(default_factory=list)


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
