"""Validated contracts for the first agent workflow."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.contracts import (
    AgentEnvelope,
    AgentName,
    ArchitectureProposal,
    CodeReviewReport,
    DocumentationPlan,
    TestStrategy,
)


class AgentRunRequest(BaseModel):
    """A user's natural-language request for an analysis and plan."""

    model_config = ConfigDict(str_strip_whitespace=True)

    goal: str = Field(min_length=10, max_length=4000)


class RequirementsSpec(BaseModel):
    """Structured requirement analyst output."""

    summary: str = Field(min_length=1, max_length=600)
    functional_requirements: list[str] = Field(min_length=1, max_length=12)
    non_functional_requirements: list[str] = Field(default_factory=list, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=12)


class PlannedTask(BaseModel):
    """One dependency-aware task in the generated plan."""

    task_id: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=600)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    assigned_agent: AgentName = "coding"
    complexity: Literal["low", "medium", "high"]


class TaskPlan(BaseModel):
    """Validated task list with dependency references checked."""

    summary: str = Field(min_length=1, max_length=600)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_task_dependencies(self) -> "TaskPlan":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique.")
        known_ids = set(task_ids)
        positions = {task_id: index for index, task_id in enumerate(task_ids)}
        dependencies = {task.task_id: task.depends_on for task in self.tasks}
        for task_id, required_ids in dependencies.items():
            if task_id in required_ids or not set(required_ids) <= known_ids:
                raise ValueError("Task dependencies must reference other tasks in this plan.")
            if any(positions[dependency] >= positions[task_id] for dependency in required_ids):
                raise ValueError("Tasks must be listed in dependency execution order.")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("Task dependencies must not contain a cycle.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)
        return self


class CodingBrief(BaseModel):
    """A code agent's bounded implementation proposal; it does not edit files."""

    summary: str = Field(min_length=1, max_length=600)
    implementation_steps: list[str] = Field(min_length=1, max_length=12)
    candidate_files: list[str] = Field(default_factory=list, max_length=12)
    acceptance_checks: list[str] = Field(min_length=1, max_length=12)
    requires_human_approval: Literal[True] = True

    @field_validator("candidate_files")
    @classmethod
    def validate_candidate_paths(cls, value: list[str]) -> list[str]:
        for file_path in value:
            path = PurePosixPath(file_path)
            if (
                not file_path.strip()
                or file_path != file_path.strip()
                or "\x00" in file_path
                or "\\" in file_path
                or not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or ":" in path.parts[0]
            ):
                raise ValueError("Candidate file paths must be safe relative paths.")
        return value


class AgentRunResponse(BaseModel):
    """Structured result from one bounded specialist-agent workflow execution."""

    run_id: str
    status: Literal["completed"]
    model: str
    requirements: RequirementsSpec
    architecture: ArchitectureProposal
    plan: TaskPlan
    coding_brief: CodingBrief
    review: CodeReviewReport
    test_strategy: TestStrategy
    documentation_plan: DocumentationPlan
    agent_messages: list[AgentEnvelope]
    completed_agents: list[str]


class OllamaHealthResponse(BaseModel):
    """Safe local model runtime status without exposing endpoint or error details."""

    status: Literal["ready", "runtime_unavailable", "model_not_configured", "model_not_found"]
    runtime_available: bool
    configured_model: str | None
    model_available: bool
    available_models: list[str]
