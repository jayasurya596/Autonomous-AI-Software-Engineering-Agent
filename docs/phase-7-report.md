# Phase 7 verification report

## Delivered

- Added a read-only repository reader rooted at `AGENT_REPOSITORIES_ROOT`. Requests select a repository using a relative directory name; absolute paths, traversal, and symbolic links are rejected.
- Repository snapshots omit Git internals, common generated folders, credential-like files and binary content. Context is limited to 40 files, 8,000 characters per excerpt and 80,000 total characters.
- Added authenticated project-owned repository analysis and code-generation endpoints.
- Added a coding agent that returns a validated change proposal. Existing files are selected by their index in the inspected snapshot; new files are restricted to one safe filename segment. Source files are not written, and repository contents are not persisted by the service.
- Added checks for repository traversal, secret filtering, context limits, unsafe generated names, duplicate files, authentication and preview-only change behavior.

## Verification

- `uv run pytest -q`: 36 passed, 6 skipped (the opt-in local-model checks are skipped by default).
- `RUN_OLLAMA_LIVE_TESTS=1 uv run pytest backend/tests/test_repository_live.py -q`: 1 passed against local Ollama. It exercised both API endpoints and confirmed the source file was unchanged.
- `uv run ruff check backend`: passed.
- `uv run mypy`: passed for 29 application files.
- Frontend production build: passed at the Phase 6 gate; no frontend files changed in Phase 7.

## Setup

Create a local repository root (ignored by Git), place a checkout inside it, and set `AGENT_REPOSITORIES_ROOT` in `.env`. Send its child directory name to the project repository endpoints. The root defaults to `.agent-repositories` relative to the API working directory.

## Limitations

This phase returns preview-only changes. It does not apply them, run project tests, persist executions, or synchronize a GitHub repository; those steps are handled by later phases. Repository names and excerpts may be included in the local model prompt, so configure the root only for repositories the local operator intends to analyze.
