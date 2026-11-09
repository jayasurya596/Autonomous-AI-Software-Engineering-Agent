"""Tenant-scoped PostgreSQL tables for executions, traces and project memory."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ExecutionRecord(Base):
    __tablename__ = "agent_executions"
    __table_args__ = (Index("ix_agent_executions_owner_created", "owner_uid", "created_at"),)

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(String(4000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resume_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    debug_summary: Mapped[str | None] = mapped_column(String(600), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    traces: Mapped[list["AgentTraceRecord"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentTraceRecord.id",
        lazy="selectin",
    )


class AgentTraceRecord(Base):
    __tablename__ = "agent_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("agent_executions.execution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    execution: Mapped[ExecutionRecord] = relationship(back_populates="traces")


class ProjectMemoryRecord(Base):
    __tablename__ = "project_memories"
    __table_args__ = (
        Index("ix_project_memories_tenant", "owner_uid", "project_id", "created_at"),
        Index(
            "ix_project_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(String(4000), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GithubConnectionRecord(Base):
    """Encrypted GitHub credential owned by one authenticated Firebase user."""

    __tablename__ = "github_connections"

    owner_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalRecord(Base):
    """A tested, owner-scoped code change awaiting explicit review."""

    __tablename__ = "code_approvals"
    __table_args__ = (
        Index("ix_code_approvals_owner_created", "owner_uid", "created_at"),
        Index("ix_code_approvals_owner_project", "owner_uid", "project_id", "status"),
    )

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    repository_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    changes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    base_hashes: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False)
    runtime: Mapped[str] = mapped_column(String(16), nullable=False)
    test_output: Mapped[str] = mapped_column(Text, nullable=False)
    test_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    github_branch: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pull_request_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    events: Mapped[list["ApprovalEventRecord"]] = relationship(
        back_populates="approval", cascade="all, delete-orphan", order_by="ApprovalEventRecord.id"
    )


class ApprovalEventRecord(Base):
    """Append-only audit of reviewer decisions and GitHub publication outcomes."""

    __tablename__ = "code_approval_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("code_approvals.approval_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approval: Mapped[ApprovalRecord] = relationship(back_populates="events")
