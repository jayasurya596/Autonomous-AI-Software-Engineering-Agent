"""Authenticated repository analysis and code-generation API contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import (
    AgentEnvelope,
    ChangeSetProposal,
    FileChangeProposal,
    RepositoryAnalysis,
)


class RepositoryAnalysisRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    repository_directory: str = Field(min_length=1, max_length=512)


class RepositoryChangeRequest(RepositoryAnalysisRequest):
    goal: str = Field(min_length=10, max_length=4000)


class RepositoryAnalysisResponse(BaseModel):
    analysis: RepositoryAnalysis
    agent_message: AgentEnvelope
    snapshot_file_count: int = Field(ge=1, le=40)
    omitted_file_count: int = Field(ge=0)


class RepositoryChangeResponse(RepositoryAnalysisResponse):
    proposal: ChangeSetProposal


class RepositoryTestRequest(RepositoryAnalysisRequest):
    runtime: Literal["python", "node"]
    changes: list[FileChangeProposal] = Field(default_factory=list, max_length=5)


class RepositoryTestResponse(BaseModel):
    status: Literal["passed", "failed", "timed_out", "output_limited"]
    runtime: Literal["python", "node"]
    exit_code: int | None
    output: str = Field(max_length=20_100)
    duration_ms: int = Field(ge=0)
    image: str
    command: list[str]
