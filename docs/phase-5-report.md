# Phase 5 Ollama and LangGraph report

## Status

Complete and verified against the local Ollama service and the `llama3.2:1b` model. No cloud inference service is configured.

## Implementation

- Added validated settings for the Ollama base URL, model, request timeout and maximum generated tokens.
- Added an async Ollama adapter that checks the local model list, requests JSON-schema output and validates each response with Pydantic.
- Added a bounded LangGraph supervisor that runs the requirement analyst, dependency-aware planner and coding proposal node in sequence.
- Added authenticated `GET /api/v1/agents/health` and `POST /api/v1/agents/runs` endpoints.
- Added structured output limits, dependency/cycle/order validation, and safe relative-path checks for proposed files.
- Added a coding brief that requires human approval. This phase does not read repositories, write files, execute code, persist runs or resume workflows.
- Added deterministic adapter, workflow and API tests plus an opt-in live Ollama API test.

## Verification

| Check | Result |
| --- | --- |
| Local Ollama | Version 0.40.15 installed for the current Windows user; `llama3.2:1b` pulled and API ready |
| Ruff | Passed |
| Mypy | Passed; 25 source files checked |
| Auth and Standard Firestore emulator suite | 28 passed, 1 skipped (opt-in live test) |
| Frontend production build | Passed |
| Firebase configuration JSON | Passed |
| Live Ollama model/API workflow | 1 passed in 71.60 seconds; health and all three graph nodes used the local model |

## Setup and run

```powershell
uv sync --extra firebase --extra agents --group dev
ollama pull llama3.2:1b
uv run uvicorn app.main:app --app-dir backend --env-file .env --reload
```

The live check can be repeated with:

```powershell
$env:RUN_OLLAMA_LIVE_TESTS = "1"
uv run pytest backend/tests/test_ollama_live.py -q
```

The emulator-backed test command was run with `APP_ENV=test`, `FIREBASE_PROJECT_ID=demo-software-agent`, `FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099` and `FIRESTORE_EMULATOR_HOST=127.0.0.1:8080`.

## Limitations

- Graph state and returned run IDs are not persisted; resume and cancellation are not implemented yet.
- The coding node creates only a proposal. Repository inspection, file changes and test execution are later phases.
- One live smoke run verifies the integration path, not model accuracy or production readiness.

## Next

Phase 6 adds specialized agent contracts. Repository analysis and actual code generation remain in later phases.
