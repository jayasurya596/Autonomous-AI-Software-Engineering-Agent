"""Semantic project memory backed by local Ollama embeddings and PostgreSQL pgvector."""

from typing import Any
from uuid import UUID

from app.persistence.database import DatabaseService
from app.services.ollama import OllamaModelAdapter


class ProjectMemoryService:
    def __init__(self, database: DatabaseService, model: OllamaModelAdapter) -> None:
        self.database = database
        self.model = model

    async def create(self, owner_uid: str, project_id: str, content: str) -> dict[str, Any]:
        embedding = await self.model.embed(content)
        memory_id = await self.database.save_memory(owner_uid, project_id, content, embedding)
        items = await self.database.list_memories(owner_uid, project_id)
        saved = next(item for item in items if item["id"] == memory_id)
        return {**saved, "id": str(saved["id"])}

    async def search(
        self, owner_uid: str, project_id: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        embedding = await self.model.embed(query)
        records = await self.database.search_memories(owner_uid, project_id, embedding, limit)
        return [{**record, "id": str(record["id"])} for record in records]

    async def list(self, owner_uid: str, project_id: str) -> list[dict[str, Any]]:
        records = await self.database.list_memories(owner_uid, project_id)
        return [{**record, "id": str(record["id"])} for record in records]

    async def delete(self, owner_uid: str, project_id: str, memory_id: UUID) -> bool:
        return await self.database.delete_memory(owner_uid, project_id, memory_id)
