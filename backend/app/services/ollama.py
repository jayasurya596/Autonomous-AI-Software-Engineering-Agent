"""Model adapter for bounded, schema-validated local or Cloudflare inference."""

import asyncio
from typing import Literal, Protocol, TypeVar

import httpx
from ollama import AsyncClient, RequestError, ResponseError
from pydantic import BaseModel, ValidationError

from app.core.settings import ApiSettings
from app.schemas.agents import OllamaHealthResponse

OutputT = TypeVar("OutputT", bound=BaseModel)
CLOUDFLARE_AI_BASE_URL = "http://workers-ai.internal/ai"


class StructuredModel(Protocol):
    """The small model interface used by the workflow and test fakes."""

    @property
    def model_name(self) -> str: ...

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[OutputT],
    ) -> OutputT: ...


class OllamaUnavailableError(RuntimeError):
    """The configured Ollama service cannot handle the request."""


class OllamaModelNotFoundError(OllamaUnavailableError):
    """The configured model has not been pulled into the local runtime."""


class InvalidModelOutputError(RuntimeError):
    """The model response did not satisfy its requested schema."""


class OllamaModelAdapter:
    """Use Ollama locally or Cloudflare Workers AI remotely, with schema validation."""

    def __init__(
        self,
        settings: ApiSettings,
        client: AsyncClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or AsyncClient(
            host=settings.ollama_base_url, timeout=settings.ollama_timeout_seconds
        )
        self._http_client = http_client or httpx.AsyncClient(
            timeout=settings.ollama_timeout_seconds
        )

    @property
    def model_name(self) -> str:
        """Return the configured model name for response metadata."""
        if self._settings.ai_provider == "cloudflare":
            return self._settings.cloudflare_model
        return self._settings.ollama_model

    async def available_models(self) -> list[str]:
        """Check the configured provider and return only safe model identifiers."""
        if self._settings.ai_provider == "cloudflare":
            try:
                model_names = [self.model_name, self._settings.cloudflare_embedding_model]
                responses = await asyncio.gather(
                    *(
                        self._http_client.get(
                            f"{CLOUDFLARE_AI_BASE_URL}/models/schema",
                            params={"model": model_name},
                        )
                        for model_name in model_names
                    )
                )
                for schema_response in responses:
                    schema_response.raise_for_status()
                    payload = schema_response.json()
                    if not isinstance(payload, dict) or payload.get("success") is not True:
                        raise ValueError("Cloudflare model schema check failed.")
            except (httpx.HTTPError, ValueError) as exc:
                raise OllamaUnavailableError(
                    "Cloudflare Workers AI is unavailable or not configured."
                ) from exc
            return sorted(set(model_names))

        try:
            list_response = await self._client.list()
        except (RequestError, ResponseError, httpx.HTTPError) as exc:
            raise OllamaUnavailableError("The local model service is unavailable.") from exc
        return sorted({model.model for model in list_response.models if model.model})

    async def health_check(self) -> OllamaHealthResponse:
        """Report runtime and configured-model readiness with safe details."""
        try:
            models = await self.available_models()
        except OllamaUnavailableError:
            return OllamaHealthResponse(
                status="runtime_unavailable",
                runtime_available=False,
                configured_model=self.model_name or None,
                model_available=False,
                available_models=[],
            )

        if not self.model_name:
            status: Literal[
                "ready", "runtime_unavailable", "model_not_configured", "model_not_found"
            ] = "model_not_configured"
        elif self.model_name not in models:
            status = "model_not_found"
        else:
            status = "ready"
        return OllamaHealthResponse(
            status=status,
            runtime_available=True,
            configured_model=self.model_name or None,
            model_available=self.model_name in models if self.model_name else False,
            available_models=models,
        )

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[OutputT],
    ) -> OutputT:
        """Request JSON-schema output and reject malformed model responses."""
        if not self.model_name:
            raise OllamaUnavailableError("No AI model is configured.")
        if self._settings.ai_provider == "cloudflare":
            try:
                cloudflare_response = await self._http_client.post(
                    f"{CLOUDFLARE_AI_BASE_URL}/v1/chat/completions",
                    json={
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": response_model.model_json_schema(),
                        },
                        "temperature": 0,
                        "max_tokens": self._settings.ollama_num_predict,
                        "stream": False,
                    },
                )
                cloudflare_response.raise_for_status()
                payload = cloudflare_response.json()
                content = payload["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                    raise OllamaModelNotFoundError(
                        "The configured Cloudflare model is not available."
                    ) from exc
                raise OllamaUnavailableError(
                    "Cloudflare Workers AI could not complete the request."
                ) from exc
            if not isinstance(content, str) or not content:
                raise InvalidModelOutputError(
                    "The model response did not contain structured output."
                )
            try:
                return response_model.model_validate_json(content)
            except ValidationError as exc:
                raise InvalidModelOutputError(
                    "The model response did not match its output schema."
                ) from exc

        try:
            ollama_response = await self._client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format=response_model.model_json_schema(),
                options={"temperature": 0, "num_predict": self._settings.ollama_num_predict},
                stream=False,
            )
        except ResponseError as exc:
            if exc.status_code == 404:
                raise OllamaModelNotFoundError(
                    "The configured model is not available in the local runtime."
                ) from exc
            raise OllamaUnavailableError(
                "The local model service could not complete the request."
            ) from exc
        except (RequestError, httpx.HTTPError) as exc:
            raise OllamaUnavailableError(
                "The local model service could not complete the request."
            ) from exc

        if ollama_response.message.content is None:
            raise InvalidModelOutputError("The model response did not contain structured output.")
        try:
            return response_model.model_validate_json(ollama_response.message.content)
        except ValidationError as exc:
            raise InvalidModelOutputError(
                "The model response did not match its output schema."
            ) from exc

    async def embed(self, text: str) -> list[float]:
        """Create a 768-dimensional memory embedding from the configured provider."""
        if self._settings.ai_provider == "cloudflare":
            try:
                cloudflare_response = await self._http_client.post(
                    f"{CLOUDFLARE_AI_BASE_URL}/v1/embeddings",
                    json={
                        "model": self._settings.cloudflare_embedding_model,
                        "input": text,
                    },
                )
                cloudflare_response.raise_for_status()
                payload = cloudflare_response.json()
                vector = payload["data"][0]["embedding"]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise OllamaUnavailableError(
                    "Cloudflare Workers AI could not create an embedding."
                ) from exc
            if not isinstance(vector, list) or len(vector) != 768:
                raise InvalidModelOutputError(
                    "The configured embedding model returned an invalid vector."
                )
            return [float(item) for item in vector]

        try:
            ollama_response = await self._client.embed(
                model=self._settings.ollama_embedding_model,
                input=text,
            )
        except ResponseError as exc:
            if exc.status_code == 404:
                raise OllamaModelNotFoundError(
                    "The configured embedding model is not available locally."
                ) from exc
            raise OllamaUnavailableError(
                "The local embedding service could not complete the request."
            ) from exc
        except (RequestError, httpx.HTTPError) as exc:
            raise OllamaUnavailableError(
                "The local embedding service could not complete the request."
            ) from exc
        if not ollama_response.embeddings or len(ollama_response.embeddings[0]) != 768:
            raise InvalidModelOutputError("The local embedding model returned an invalid vector.")
        return list(ollama_response.embeddings[0])

    async def close(self) -> None:
        """Close the underlying async HTTP connection pool."""
        await self._client.close()  # type: ignore[no-untyped-call]
        await self._http_client.aclose()

