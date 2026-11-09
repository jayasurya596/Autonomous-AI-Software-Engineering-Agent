"""Onboarding profile schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DeveloperRole = Literal["student", "developer", "ai_engineer", "technical_lead", "other"]
ExperienceLevel = Literal["beginner", "intermediate", "advanced"]
PreferredLanguage = Literal[
    "Python",
    "TypeScript",
    "JavaScript",
    "Java",
    "Go",
    "Rust",
    "C#",
    "C++",
    "Swift",
    "Kotlin",
    "Other",
]


class UserProfileInput(BaseModel):
    """User-editable profile values; UID and email come from verified auth claims."""

    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str = Field(min_length=2, max_length=120)
    developer_role: DeveloperRole
    experience_level: ExperienceLevel
    preferred_languages: list[PreferredLanguage] = Field(min_length=1, max_length=10)
    preferred_model: str | None = Field(default=None, max_length=100)
    development_environment: str | None = Field(default=None, max_length=200)
    github_username: str | None = Field(default=None, max_length=39)

    @field_validator("preferred_languages")
    @classmethod
    def validate_languages(cls, languages: list[PreferredLanguage]) -> list[PreferredLanguage]:
        if len({language.casefold() for language in languages}) != len(languages):
            raise ValueError("Preferred languages must be unique.")
        return languages


class UserProfileResponse(UserProfileInput):
    uid: str
    email: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
