"""Validated API runtime settings read from environment variables."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Configuration shared by middleware and API initialization."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: str = "development"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    api_rate_limit_requests: int = Field(default=120, ge=1, le=100_000)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1, le=50_000_000)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", min_length=8, max_length=512)
    ollama_model: str = Field(default="llama3.2:1b", max_length=128)
    ollama_timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    ollama_num_predict: int = Field(default=3072, ge=128, le=8192)
    ollama_embedding_model: str = Field(default="nomic-embed-text", max_length=128)
    ai_provider: Literal["ollama", "cloudflare"] = "ollama"
    cloudflare_model: str = Field(
        default="@cf/meta/llama-3.3-70b-instruct-fp8-fast", max_length=128
    )
    cloudflare_embedding_model: str = Field(
        default="@cf/baai/bge-base-en-v1.5", max_length=128
    )
    r2_bucket_name: str | None = Field(default=None, max_length=63)
    max_repository_upload_bytes: int = Field(default=20_000_000, ge=1, le=50_000_000)
    agent_repositories_root: str = Field(
        default=".agent-repositories", min_length=1, max_length=1024
    )
    sandbox_python_image: str = Field(
        default="agent-sandbox-python:local", min_length=1, max_length=128
    )
    sandbox_node_image: str = Field(
        default="agent-sandbox-node:local", min_length=1, max_length=128
    )
    sandbox_timeout_seconds: int = Field(default=120, ge=1, le=600)
    database_url: str | None = Field(default=None, max_length=1024)
    github_credentials_encryption_key: str | None = Field(default=None, max_length=128)

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        if not origins or "*" in origins:
            raise ValueError("CORS_ORIGINS must contain explicit origins and cannot use '*'.")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("Each CORS origin must be an HTTP or HTTPS origin without a path.")
        return ",".join(origins)

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip().rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("OLLAMA_BASE_URL must be an HTTP or HTTPS origin without credentials.")
        return f"{parsed.scheme}://{parsed.netloc}"

    @field_validator("ollama_model")
    @classmethod
    def validate_ollama_model(cls, value: str) -> str:
        normalized = value.strip()
        if any(character.isspace() for character in normalized):
            raise ValueError("OLLAMA_MODEL must not contain whitespace.")
        return normalized

    @field_validator("ollama_embedding_model")
    @classmethod
    def validate_embedding_model(cls, value: str) -> str:
        normalized = value.strip()
        if any(character.isspace() for character in normalized):
            raise ValueError("OLLAMA_EMBEDDING_MODEL must not contain whitespace.")
        return normalized

    @field_validator("agent_repositories_root")
    @classmethod
    def validate_agent_repositories_root(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("AGENT_REPOSITORIES_ROOT must be a valid local path.")
        return normalized

    @property
    def allowed_origins(self) -> list[str]:
        """Return normalized origins for Starlette's CORS middleware."""
        return self.cors_origins.split(",")

    @property
    def r2_repository_storage_configured(self) -> bool:
        """Return whether the Cloudflare R2 bucket binding has been configured."""
        return bool(self.r2_bucket_name and self.r2_bucket_name.strip())
