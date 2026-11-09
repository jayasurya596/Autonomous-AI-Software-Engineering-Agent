"""Project request and response schemas."""

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=2, max_length=100)
    purpose: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="Python", min_length=1, max_length=32)
    framework: str | None = Field(default=None, max_length=64)
    repository_url: str | None = Field(default=None, max_length=2048)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Repository URL must be HTTP or HTTPS without embedded credentials.")
        return value


class ProjectResponse(ProjectInput):
    id: str
    owner_uid: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectPage(BaseModel):
    items: list[ProjectResponse]
    next_cursor: str | None = None
