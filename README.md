# Autonomous AI Software Engineering Agent

An autonomous, multi-agent platform for real software development workflows. The build follows the supplied PRD in gated phases: each phase is implemented and verified before the next begins.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Project structure and environment setup | Complete |
| 2 | Firebase authentication and onboarding | Complete against local emulator |
| 3 | Firestore integration and project management | Complete against Standard emulator |
| 4 | FastAPI APIs, middleware and authorization | Complete against local emulators |
| 5 | Ollama and LangGraph orchestration | Complete against local Ollama |
| 6 | Specialized agents and structured contracts | Complete |
| 7 | Repository analysis and code generation | Complete |
| 8 | Docker sandbox and automated testing | Complete |
| 9 | Debugging, recovery and persistent memory | Complete |
| 10 | GitHub integration, approvals and dashboard | Complete |
| 11 | Testing, security validation and deployment | Complete; Cloudflare deployment and live Firebase, Neon, Workers AI, R2, and sandbox checks passed |

Phase reports: [`docs/phase-1-report.md`](docs/phase-1-report.md), [`docs/phase-2-report.md`](docs/phase-2-report.md), [`docs/phase-3-report.md`](docs/phase-3-report.md), [`docs/phase-4-report.md`](docs/phase-4-report.md), [`docs/phase-5-report.md`](docs/phase-5-report.md), [`docs/phase-6-report.md`](docs/phase-6-report.md), [`docs/phase-7-report.md`](docs/phase-7-report.md), [`docs/phase-8-report.md`](docs/phase-8-report.md), [`docs/phase-9-report.md`](docs/phase-9-report.md), [`docs/phase-10-report.md`](docs/phase-10-report.md) and [`docs/phase-11-report.md`](docs/phase-11-report.md).

See [`docs/deployment.md`](docs/deployment.md) for local startup, readiness checks and OCI setup. See [`docs/cloudflare-deployment.md`](docs/cloudflare-deployment.md) for the Cloudflare Workers/Containers setup.

## Repository layout

- `backend/` — FastAPI service and Python application packages.
- `frontend/` — React, TypeScript and Vite application.
- `infra/` — local development infrastructure.
- `docs/` — architecture and setup notes.

## Local prerequisites

- Python 3.11–3.13
- Node.js 22.12+ (24 LTS is suitable)
- `uv` for Python environments and dependency locking
- Docker Desktop with Docker Compose for PostgreSQL/pgvector
- Java 21 for local Firebase emulators; a portable copy is ignored under `.tools/`
- Ollama for local model inference; the setup below uses `llama3.2:1b`

## Setup

1. Copy `.env.example` to `.env` and replace the local database password placeholder.
   The Firebase values in the example target a local demo emulator and do not use a cloud project.
2. Create the Python environment and install base plus development dependencies:

   ```powershell
   uv sync --extra firebase --extra agents --extra database --group dev
   ```

3. Install frontend dependencies:

   ```powershell
   cd frontend
   npm install
   cd ..
   ```

4. Start PostgreSQL with pgvector (the default host port is 5433):

   ```powershell
   docker compose --env-file .env -f infra/compose.yaml up -d postgres
   ```

5. In a separate terminal, start the Firebase Authentication and Standard Firestore emulators (first run downloads the emulator runtime):

   ```powershell
   .\scripts\start-firebase-emulators.ps1
   ```

6. Install Ollama for Windows from [ollama.com/download](https://ollama.com/download), then pull the configured local model:

   ```powershell
   ollama pull llama3.2:1b
   ollama pull nomic-embed-text
   ```

   Set `OLLAMA_BASE_URL`, `OLLAMA_MODEL` and the timeout/output limits in `.env` as needed. The default model is about 1.3 GB.

7. Start the API:

   ```powershell
   uv run uvicorn app.main:app --app-dir backend --env-file .env --reload
   ```

8. Start the frontend in another terminal:

   ```powershell
   cd frontend
   npm run dev
   ```

The API exposes `/health`, `/api/v1/monitoring/health`, `/api/v1/monitoring/ready`, `/api/v1/auth/me`, `/api/v1/users/profile`, `/api/v1/workspaces`, `/api/v1/projects`, `/api/v1/agents/health`, `/api/v1/agents/runs`, `/api/v1/executions`, project repository/memory routes, `/api/v1/approvals`, and `/api/v1/github/connection`. Protected routes require a verified Firebase account. `/agents/runs` returns the structured specialist analysis synchronously; `/executions` creates a persistent, cancellable and resumable job stored in PostgreSQL. Persistent execution, memory, approval, and GitHub connection routes require `DATABASE_URL` and the database extra. `/health` is process liveness; `/monitoring/ready` checks required PostgreSQL and Ollama dependencies.

For Phase 7, put local checkouts under `AGENT_REPOSITORIES_ROOT` and call `/api/v1/repositories/projects/{project_id}/analyze` or `/api/v1/repositories/projects/{project_id}/changes` with the child directory name. These authenticated endpoints verify project ownership and return bounded analysis or a preview-only proposal. They never modify source files. Keep the repository root limited to code you intend to send to the configured local model.

For Phase 8, build the local Python and Node sandbox images as shown in [`docs/phase-8-report.md`](docs/phase-8-report.md). The authenticated `POST /api/v1/repositories/projects/{project_id}/test` endpoint accepts a repository directory, runtime profile and proposed file changes. Its test command is fixed by profile; the request cannot execute arbitrary shell commands.

For Phase 9, create and search owner/project-scoped memories under `/api/v1/projects/{project_id}/memory`. Add `DATABASE_URL` to `.env`; the local Compose service binds to port 5433 so a separate service can keep using 5432. The seven-step graph makes bounded schema/transient retries, writes a PostgreSQL checkpoint after each step and can resume cancelled or interrupted runs up to five times.

For Phase 10, set a private `GITHUB_CREDENTIALS_ENCRYPTION_KEY` in `.env`. Generate one with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and copy it into `.env`. In the project workbench, connect a GitHub personal access token that can write repository contents and create pull requests. Tokens are validated and encrypted in PostgreSQL. The agent can analyze a configured local checkout and propose code; requesting review reruns the fixed sandbox test profile and stores a review diff. Only the explicit **Approve and create pull request** action writes a new `codex/approval-…` branch and pull request. The service checks changed-file hashes against the selected remote default branch and refuses stale proposals. It never merges a pull request or writes generated code into the local checkout.

API middleware adds request IDs, safe JSON error responses, explicit-origin CORS, security headers, a configurable request-body limit and a configurable sliding-window rate limit. The rate limiter is process-local and keyed by the immediate client address; use a shared limiter and trusted proxy configuration before running multiple API workers behind a load balancer.

Each full agent graph makes seven bounded specialist model calls. Synchronous `/agents/runs` requests keep state for that request. Durable `/executions` jobs checkpoint graph state to PostgreSQL, preserve sanitized trace summaries, and can resume after cancellation or process restart. When `OLLAMA_BASE_URL` points to the local default, prompts are sent to the local Ollama service.

To run the live Phase 5 check against the configured local model:

```powershell
$env:RUN_OLLAMA_LIVE_TESTS = "1"
uv run pytest backend/tests/test_ollama_live.py -q
```

## Live deployments

The full app is live at <https://autonomous-ai-software-engineering-agent.penchalajayasuryachappidi.workers.dev>. Cloudflare serves the frontend and FastAPI container; Firebase provides verified Email/Password sign-in and Standard Firestore; Neon provides PostgreSQL and pgvector; Workers AI runs specialist agents and embeddings; R2 stores private source snapshots. The isolated Python and Node runners execute within restricted containers.

The Firebase Hosting URL <https://agent-swe-20261109-8f03.web.app> remains a frontend-only preview. Use the Cloudflare URL for the complete application. GitHub publishing requires each signed-in user to connect a GitHub token and repository in the app. See [`docs/cloudflare-deployment.md`](docs/cloudflare-deployment.md) for operations and deployment instructions. The OCI configuration remains under `infra/oci/`.

For a separate production backend deployment, replace the demo `VITE_FIREBASE_*` and `FIREBASE_PROJECT_ID` values with the web app/project configuration, set `FIREBASE_ADMIN_CREDENTIALS_PATH` or use Application Default Credentials, and clear `FIREBASE_AUTH_EMULATOR_HOST` and `FIRESTORE_EMULATOR_HOST`. Review and deploy `firestore.rules` and `firestore.indexes.json` for the target project before use.

## Security basics

- Keep `.env`, Firebase credentials and all tokens out of Git.
- `.env.example` contains demo-project emulator values and placeholders, never real credentials.
- `GITHUB_CREDENTIALS_ENCRYPTION_KEY` must remain private and backed up; losing it makes saved GitHub tokens undecryptable. Code proposals and test output are retained with approval records in PostgreSQL for review, and this project does not encrypt those records at the application layer.
- GitHub tokens are encrypted with Fernet before they are stored. Use the smallest token permissions that permit repository contents writes and pull request creation, and disconnect/revoke tokens when they are no longer needed.
- The local database binds to loopback; do not expose it to a public network.
- Both emulator hosts are blocked outside development and test environments.
- Firestore client rules allow authenticated owner reads and deny all client writes. The FastAPI server uses the Admin SDK, which bypasses rules, so its ownership checks are required for writes and privileged reads.
- Idempotency records keep only the owner, operation, request hash, resource pointer and a 24-hour expiry. `firestore.indexes.json` configures TTL for deployed databases; the policy has not been deployed to a cloud project.
- Repository analysis and preview-only code generation are available in Phase 7; Python and Node container test profiles are available in Phase 8; persistent workflows and semantic memory are available in Phase 9.
