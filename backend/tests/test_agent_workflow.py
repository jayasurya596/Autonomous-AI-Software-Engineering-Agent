"""Deterministic checks for the LangGraph supervisor and structured state."""

import asyncio
from typing import TypeVar

import pytest
from app.agents.contracts import (
    ArchitectureProposal,
    ChangeSetDraft,
    CodeReviewReport,
    DebugDiagnosis,
    DocumentationPlan,
    RepositoryAnalysis,
)
from app.agents.contracts import (
    TestStrategy as AgentTestStrategy,
)
from app.schemas.agents import CodingBrief, RequirementsSpec, TaskPlan
from app.services.agent_workflow import AgentWorkflow
from app.services.ollama import (
    InvalidModelOutputError,
    OllamaModelNotFoundError,
    OllamaUnavailableError,
)
from pydantic import BaseModel, ValidationError

OutputT = TypeVar("OutputT", bound=BaseModel)


class FakeModel:
    model_name = "test-model"

    def __init__(self) -> None:
        self.calls: list[type[BaseModel]] = []
        self.prompts: list[tuple[str, str]] = []

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[OutputT],
    ) -> OutputT:
        self.calls.append(response_model)
        self.prompts.append((system_prompt, user_prompt))
        if response_model is RequirementsSpec:
            data = {
                "summary": "Add a health endpoint.",
                "functional_requirements": ["Return a health status."],
                "non_functional_requirements": ["Respond quickly."],
                "constraints": ["Use FastAPI."],
                "assumptions": [],
                "risks": [],
                "open_questions": [],
            }
        elif response_model is TaskPlan:
            data = {
                "summary": "Implement and validate the endpoint.",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "title": "Add route",
                        "description": "Create the health route.",
                        "depends_on": [],
                        "assigned_agent": "coding",
                        "complexity": "low",
                    },
                    {
                        "task_id": "task_2",
                        "title": "Add checks",
                        "description": "Verify the response.",
                        "depends_on": ["task_1"],
                        "assigned_agent": "coding",
                        "complexity": "low",
                    },
                ],
                "risks": [],
            }
        elif response_model is ArchitectureProposal:
            data = {
                "summary": "A small API route with a focused health response.",
                "modules": [{"name": "API route", "responsibility": "Serve health status."}],
                "security_considerations": ["Keep response data non-sensitive."],
            }
        elif response_model is CodingBrief:
            data = {
                "summary": "Propose a route and a focused check.",
                "implementation_steps": ["Add the route", "Add an endpoint check"],
                "candidate_files": ["backend/app/api/monitoring.py"],
                "acceptance_checks": ["GET returns status ok"],
                "requires_human_approval": True,
            }
        elif response_model is CodeReviewReport:
            data = {
                "scope": "Plan and coding brief only.",
                "verdict": "brief_ready",
                "warnings": ["No source diff was supplied."],
            }
        elif response_model is AgentTestStrategy:
            data = {
                "test_cases": [
                    {
                        "name": "Health response",
                        "purpose": "Check the route returns a status.",
                        "test_type": "integration",
                        "priority": "high",
                    }
                ],
                "not_executed": True,
            }
        elif response_model is DocumentationPlan:
            data = {
                "items": [
                    {
                        "document": "README.md",
                        "update_summary": "Describe the health endpoint.",
                        "evidence_needed": ["Verified route behavior"],
                    }
                ],
                "not_written": True,
            }
        elif response_model is RepositoryAnalysis:
            data = {
                "summary": "The project has a small API route module.",
                "relevant_files": [
                    {
                        "path": "backend/app/api/monitoring.py",
                        "reason": "Defines the API routes.",
                        "symbols": ["router"],
                    }
                ],
                "conventions": ["Use FastAPI routers."],
                "unanswered_questions": [],
            }
        elif response_model is ChangeSetDraft:
            data = {
                "summary": "Add a focused API route.",
                "edits": [
                    {
                        "file_index": 0,
                        "content": "from fastapi import APIRouter\n",
                        "rationale": "Adds the requested API route.",
                    }
                ],
                "acceptance_checks": ["The route returns the expected status."],
            }
        elif response_model is DebugDiagnosis:
            data = {
                "root_cause_summary": "The model output did not satisfy the expected schema.",
                "hypotheses": [
                    {
                        "cause": "A required structured field was omitted.",
                        "confidence": 0.7,
                        "supporting_evidence": "The schema validation failed.",
                        "recommended_fix": "Retry once with the required fields.",
                    }
                ],
                "retry_recommended": False,
                "requires_user_input": True,
            }
        else:
            raise AssertionError(f"Unsupported output schema: {response_model.__name__}")
        return response_model.model_validate(data)


def test_supervisor_runs_specialist_agents_in_order_with_contracts() -> None:
    model = FakeModel()
    workflow = AgentWorkflow(model)

    result = asyncio.run(workflow.run("Add a small health endpoint to the API."))

    assert model.calls == [
        RequirementsSpec,
        ArchitectureProposal,
        TaskPlan,
        CodingBrief,
        CodeReviewReport,
        AgentTestStrategy,
        DocumentationPlan,
    ]
    assert result["completed_agents"] == [
        "requirement_analyst",
        "architecture",
        "planner",
        "coding",
        "code_review",
        "testing",
        "documentation",
    ]
    assert result["plan"].tasks[1].depends_on == ["task_1"]
    assert result["coding_brief"].requires_human_approval is True
    assert len(result["agent_messages"]) == 7
    assert result["agent_messages"][0].invocation.execution_id == result["run_id"]
    assert result["test_strategy"].not_executed is True
    assert result["documentation_plan"].not_written is True


def test_agent_model_validation_and_transient_errors_retry_once() -> None:
    class RetryOnceModel(FakeModel):
        def __init__(self, failure: Exception) -> None:
            super().__init__()
            self.failure = failure
            self.first_call = True
            self.requirements_attempts = 0

        async def generate_structured(self, system_prompt, user_prompt, response_model):
            if response_model is RequirementsSpec:
                self.requirements_attempts += 1
                if self.first_call:
                    self.first_call = False
                    raise self.failure
            return await super().generate_structured(system_prompt, user_prompt, response_model)

    for error in (
        InvalidModelOutputError("invalid structured response"),
        OllamaUnavailableError("temporary connection failure"),
    ):
        model = RetryOnceModel(error)
        result = asyncio.run(AgentWorkflow(model).run("Add a small health endpoint."))
        assert model.requirements_attempts == 2
        assert result["completed_agents"][-1] == "documentation"


def test_missing_model_does_not_retry_agent_call() -> None:
    class MissingModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def generate_structured(self, system_prompt, user_prompt, response_model):
            self.attempts += 1
            raise OllamaModelNotFoundError("model missing")

    model = MissingModel()
    with pytest.raises(OllamaModelNotFoundError):
        asyncio.run(AgentWorkflow(model).run("Add a small health endpoint."))
    assert model.attempts == 1


def test_task_plan_rejects_dependency_cycles() -> None:
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(
            {
                "summary": "Cyclic plan",
                "tasks": [
                    {
                        "task_id": "one",
                        "title": "One",
                        "description": "First task",
                        "depends_on": ["two"],
                        "complexity": "low",
                    },
                    {
                        "task_id": "two",
                        "title": "Two",
                        "description": "Second task",
                        "depends_on": ["one"],
                        "complexity": "medium",
                    },
                ],
            }
        )


def test_task_plan_rejects_tasks_outside_dependency_order() -> None:
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(
            {
                "summary": "Incorrectly ordered plan",
                "tasks": [
                    {
                        "task_id": "second",
                        "title": "Second task",
                        "description": "Depends on the first task.",
                        "depends_on": ["first"],
                        "complexity": "low",
                    },
                    {
                        "task_id": "first",
                        "title": "First task",
                        "description": "Must run first.",
                        "depends_on": [],
                        "complexity": "low",
                    },
                ],
            }
        )


def test_coding_brief_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        CodingBrief.model_validate(
            {
                "summary": "Unsafe file proposal",
                "implementation_steps": ["Write a file"],
                "candidate_files": ["../../outside.txt"],
                "acceptance_checks": ["Check the file"],
                "requires_human_approval": True,
            }
        )


def test_coding_brief_rejects_empty_normalized_path() -> None:
    with pytest.raises(ValidationError):
        CodingBrief.model_validate(
            {
                "summary": "Invalid file proposal",
                "implementation_steps": ["Write a file"],
                "candidate_files": ["."],
                "acceptance_checks": ["Check the file"],
                "requires_human_approval": True,
            }
        )


def test_repository_analysis_and_code_generation_use_contracts_without_writing_files() -> None:
    from app.agents.contracts import RepositoryFileContext, RepositorySnapshot

    async def run() -> tuple[object, object, FakeModel]:
        model = FakeModel()
        workflow = AgentWorkflow(model)
        snapshot = RepositorySnapshot(
            project_summary="Example Python API",
            files=[
                RepositoryFileContext(
                    path="backend/app/api/monitoring.py",
                    language="py",
                    excerpt="router = APIRouter()",
                )
            ],
        )
        analysis, analysis_envelope = await workflow.analyze_repository("run-1", snapshot, "p-1")
        proposal, proposal_envelope = await workflow.generate_changes(
            "run-1", "Add a status endpoint to the API.", snapshot, analysis, "p-1"
        )
        return (analysis_envelope, proposal_envelope, model)

    analysis_envelope, proposal_envelope, model = asyncio.run(run())
    assert len(model.calls) == 2
    assert "router = APIRouter()" in model.prompts[0][1]
    assert analysis_envelope.invocation.agent_name == "repository_analyst"
    assert proposal_envelope.invocation.agent_name == "coding"
    assert proposal_envelope.warnings == [
        "Changes are a proposal only; no source files were written or tested."
    ]
