# Phase 9 verification report

## Delivered

- Added PostgreSQL tables for executions, agent traces and project memories. Execution and memory reads are scoped by the verified Firebase owner; repository excerpts, code proposals and full prompts are not stored in execution traces.
- Added a Postgres-backed LangGraph checkpointer with strict MessagePack deserialization, startup recovery of interrupted jobs, cancellation, resumable checkpoints and a five-resume cap.
- Added one bounded model/schema retry per specialist call. Missing models are not retried. Failed runs receive a sanitized error category and a bounded debugging diagnosis; permission and policy failures are not automatically retried.
- Added local `nomic-embed-text` embeddings and pgvector cosine search for owner/project-scoped memory. Retrieved user notes are treated as untrusted context and cannot override a new request.
- Added authenticated execution create/list/detail/cancel/resume APIs and project-memory create/list/search/delete APIs.
- Configured the Windows selector event loop needed by psycopg async I/O.

## Verification

- `uv run pytest -q`: 44 passed, 8 skipped (opt-in Ollama, Docker and PostgreSQL checks are skipped by default).
- `RUN_POSTGRES_LIVE_TESTS=1 uv run pytest backend/tests/test_phase9_persistence_live.py -q`: 1 passed against local PostgreSQL/pgvector and Ollama embeddings. It verified checkpoint persistence, cancellation, resume, seven traces, semantic retrieval, memory injection into the next workflow and cross-user isolation.
- `uv run ruff check backend`: passed.
- `uv run mypy`: passed for 37 application files.
- PostgreSQL/pgvector initialized in Docker on `127.0.0.1:5433`; the host's existing 5432 listener was left untouched.

## Setup

```powershell
uv sync --extra firebase --extra agents --extra database --group dev
ollama pull llama3.2:1b
ollama pull nomic-embed-text
docker compose --env-file .env -f infra/compose.yaml up -d postgres
```

Set `DATABASE_URL` in `.env` to the `127.0.0.1:5433` mapping. Start the API with `--env-file .env` to enable persistent execution and memory routes.

## Limitations

Persistent jobs and memory require the local PostgreSQL service and an installed embedding model. API-process shutdown interrupts active work at the latest durable graph checkpoint; the execution can be resumed after restart. GitHub operations and human approval workflows are implemented in Phase 10.
