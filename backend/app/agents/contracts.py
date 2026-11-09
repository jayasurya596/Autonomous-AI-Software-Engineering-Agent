"""Versioned, validated contracts shared by specialist agents."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentName = Literal[
    "supervisor",
    "requirement_analyst",
    "architecture",
    "planner",
    "repository_analyst",
    "coding",
    "code_review",
    "testing",
    "debugging",
    "documentation",
]
AgentTool = Literal[
    "local_model",
    "repository_read",
    "patch_write",
    "sandbox_test",
    "git_read",
    "git_write",
]


class AgentInvocation(BaseModel):
    """Input envelope declaring scope, schema versions and allowed tools."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    agent_name: AgentName
    input_schema_version: Literal["1.0"] = "1.0"
    objective: str = Field(min_length=1, max_length=2000)
    context_references: list[str] = Field(default_factory=list, max_length=32)
    allowed_tools: list[AgentTool] = Field(default_factory=list, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=16)
    output_schema_version: Literal["1.0"] = "1.0"


class AgentEvidence(BaseModel):
    """A verifiable reference produced by a deterministic tool or context source."""

    source: Literal["user_input", "repository", "test", "tool"]
    summary: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=512)


class AgentFailure(BaseModel):
    """Sanitized failure classification for an agent invocation."""

    category: Literal[
        "validation", "permission", "model", "repository", "sandbox", "transient", "internal"
    ]
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class AgentEnvelope(BaseModel):
    """Validated result envelope; model claims remain distinct from evidence."""

    model_config = ConfigDict(extra="forbid")

    invocation: AgentInvocation
    status: Literal["completed", "failed", "waiting_for_input"]
    result: dict[str, object] | None = None
    evidence: list[AgentEvidence] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(default_factory=list, max_length=16)
    error: AgentFailure | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "AgentEnvelope":
        if self.status == "completed" and (self.result is None or self.error is not None):
            raise ValueError("Completed agents must include a result and cannot include an error.")
        if self.status == "failed" and self.error is None:
            raise ValueError("Failed agents must include a classified error.")
        return self


class ArchitectureModule(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    responsibility: str = Field(min_length=1, max_length=500)
    proposed_interfaces: list[str] = Field(default_factory=list, max_length=12)


class ArchitectureProposal(BaseModel):
    """Architecture agent result based on requirements, not uninspected code."""

    summary: str = Field(min_length=1, max_length=600)
    modules: list[ArchitectureModule] = Field(min_length=1, max_length=12)
    data_changes: list[str] = Field(default_factory=list, max_length=12)
    security_considerations: list[str] = Field(min_length=1, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)


class ReviewFinding(BaseModel):
    severity: Literal["critical", "high", "medium", "low", "suggestion"]
    category: Literal[
        "correctness", "security", "performance", "reliability", "maintainability", "scope"
    ]
    finding: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=500)
    recommendation: str = Field(min_length=1, max_length=500)
    confirmed: bool


class CodeReviewReport(BaseModel):
    """Review findings explicitly limited to the plan and coding brief."""

    scope: str = Field(min_length=1, max_length=300)
    verdict: Literal["brief_ready", "needs_revision", "requires_user_input"]
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=16)
    warnings: list[str] = Field(default_factory=list, max_length=12)


class TestCaseProposal(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=400)
    test_type: Literal["unit", "integration", "security", "regression"]
    priority: Literal["high", "medium", "low"]


class TestStrategy(BaseModel):
    """A test plan only; actual execution is implemented in Phase 8."""

    test_cases: list[TestCaseProposal] = Field(min_length=1, max_length=16)
    quality_checks: list[str] = Field(default_factory=list, max_length=12)
    not_executed: Literal[True] = True
    risks: list[str] = Field(default_factory=list, max_length=12)


class DocumentationItem(BaseModel):
    document: str = Field(min_length=1, max_length=120)
    update_summary: str = Field(min_length=1, max_length=400)
    evidence_needed: list[str] = Field(default_factory=list, max_length=8)


class DocumentationPlan(BaseModel):
    """Documentation agent plan based on workflow outputs."""

    items: list[DocumentationItem] = Field(min_length=1, max_length=12)
    not_written: Literal[True] = True


class RepositoryFileContext(BaseModel):
    """A bounded, already-sanitized file excerpt supplied by repository tooling."""

    path: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9_@+ .-]+(/[A-Za-z0-9_@+ .-]+)*$",
        description=(
            "Root-relative POSIX path only, such as app.py or src/main.py. "
            "Never use an absolute path or a leading ./ prefix."
        ),
    )
    language: str | None = Field(default=None, max_length=32)
    excerpt: str = Field(max_length=8000)


class RepositorySnapshot(BaseModel):
    """Bounded repository context passed to the Phase 7 repository analyst."""

    project_summary: str = Field(min_length=1, max_length=1000)
    revision: str | None = Field(default=None, max_length=128)
    files: list[RepositoryFileContext] = Field(min_length=1, max_length=40)
    omitted_file_count: int = Field(default=0, ge=0, le=100_000)


class RelevantFile(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=1, max_length=400)
    symbols: list[str] = Field(default_factory=list, max_length=24)


class RepositoryAnalysis(BaseModel):
    summary: str = Field(min_length=1, max_length=800)
    relevant_files: list[RelevantFile] = Field(min_length=1, max_length=20)
    conventions: list[str] = Field(default_factory=list, max_length=16)
    dependencies: list[str] = Field(default_factory=list, max_length=32)
    unanswered_questions: list[str] = Field(default_factory=list, max_length=12)
    omitted_context_warning: str | None = Field(default=None, max_length=300)


class FileChangeProposal(BaseModel):
    """One generated text file for a reviewable, non-persistent change proposal."""

    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=20_000)
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_path(self) -> "FileChangeProposal":
        path = PurePosixPath(self.path)
        lowered_parts = [part.casefold() for part in path.parts]
        basename = lowered_parts[-1] if lowered_parts else ""
        sensitive = (
            basename in {".env", "id_rsa", "id_ed25519"}
            or "secret" in basename
            or "credential" in basename
            or basename.endswith((".pem", ".key", ".p12", ".pfx"))
            or any(part in {".git", ".ssh"} for part in lowered_parts)
        )
        if (
            self.path != self.path.strip()
            or "\x00" in self.path
            or "\\" in self.path
            or path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or ":" in path.parts[0]
            or sensitive
        ):
            raise ValueError("Generated file paths must be safe, non-sensitive relative paths.")
        return self


class ChangeSetProposal(BaseModel):
    """Bounded code-generation output awaiting testing and explicit approval."""

    summary: str = Field(min_length=1, max_length=800)
    files: list[FileChangeProposal] = Field(min_length=1, max_length=5)
    acceptance_checks: list[str] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> "ChangeSetProposal":
        paths = [change.path.casefold() for change in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("A generated change set cannot contain duplicate file paths.")
        return self


class ProposedFileEdit(BaseModel):
    """Model output targets an indexed excerpt instead of inventing a host path."""

    file_index: int = Field(ge=0, le=39)
    content: str = Field(max_length=20_000)
    rationale: str = Field(min_length=1, max_length=500)


class ProposedNewFile(BaseModel):
    """A new file name is restricted to one safe segment under the repo root."""

    filename: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_@+.-]+$",
    )
    content: str = Field(max_length=20_000)
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_filename(self) -> "ProposedNewFile":
        name = self.filename.casefold()
        if (
            self.filename in {".", ".."}
            or name in {".env", "id_rsa", "id_ed25519"}
            or "secret" in name
            or "credential" in name
            or name.endswith((".pem", ".key", ".p12", ".pfx"))
        ):
            raise ValueError("Generated file name is unsafe or sensitive.")
        return self


class ChangeSetDraft(BaseModel):
    """Untrusted model output, mapped deterministically to safe repository paths."""

    summary: str = Field(min_length=1, max_length=800)
    edits: list[ProposedFileEdit] = Field(default_factory=list, max_length=5)
    new_files: list[ProposedNewFile] = Field(default_factory=list, max_length=5)
    acceptance_checks: list[str] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "ChangeSetDraft":
        if not self.edits and not self.new_files:
            raise ValueError("A generated change set must include an edit or new file.")
        if len(self.edits) + len(self.new_files) > 5:
            raise ValueError("A generated change set cannot exceed five files.")
        if len({edit.file_index for edit in self.edits}) != len(self.edits):
            raise ValueError("A generated change set cannot edit the same file twice.")
        names = [item.filename.casefold() for item in self.new_files]
        if len(names) != len(set(names)):
            raise ValueError("A generated change set cannot contain duplicate file names.")
        return self


class FailureContext(BaseModel):
    """Bounded, sanitized test or tool failure context for debugging."""

    execution_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    failure_category: Literal["test_failure", "timeout", "tool_error", "validation"]
    summary: str = Field(min_length=1, max_length=1000)
    output_tail: str = Field(default="", max_length=6000)
    attempt: int = Field(default=0, ge=0, le=5)


class DebugHypothesis(BaseModel):
    cause: str = Field(min_length=1, max_length=400)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: str = Field(min_length=1, max_length=500)
    recommended_fix: str = Field(min_length=1, max_length=600)


class DebugDiagnosis(BaseModel):
    """A diagnosis; the agent cannot apply fixes or issue commands in Phase 6."""

    root_cause_summary: str = Field(min_length=1, max_length=600)
    hypotheses: list[DebugHypothesis] = Field(min_length=1, max_length=8)
    retry_recommended: bool
    requires_user_input: bool


class AgentCatalogItem(BaseModel):
    id: AgentName
    description: str = Field(min_length=1, max_length=300)
    workflow_status: Literal["active", "available", "phase_pending"]
    allowed_tools: list[AgentTool] = Field(default_factory=list, max_length=8)
