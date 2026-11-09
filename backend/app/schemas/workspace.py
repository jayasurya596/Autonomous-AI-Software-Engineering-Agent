"""Workspace request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)


class WorkspaceResponse(WorkspaceInput):
    id: str
    owner_uid: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkspacePage(BaseModel):
    items: list[WorkspaceResponse]
    next_cursor: str | None = None
