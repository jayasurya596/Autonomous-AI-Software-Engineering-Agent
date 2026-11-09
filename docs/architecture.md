# Architecture

The project is split into a React/TypeScript frontend, a Python/FastAPI backend, agent and application-service packages, and local infrastructure. Firebase Authentication and Firestore own identity and workspace/project metadata. PostgreSQL with pgvector owns execution records, traces and semantic project memory. Ollama with LangGraph provides local inference and resumable orchestration. Generated code runs in restricted, temporary Docker workspaces.

The backend derives identity from verified Firebase tokens and checks ownership on each protected resource. Sensitive Git operations remain approval-gated. Local development uses Firebase Authentication and Standard Firestore emulators; no cloud Firebase project is configured.

## Phase 5 orchestration

The FastAPI backend owns one configured Ollama async client and a compiled LangGraph `StateGraph`. An authenticated request moves through a bounded supervisor, requirement analyst, planner and coding node. Ollama responses use Pydantic JSON schemas and are validated before they enter graph state. The API returns a run ID, structured requirements, an execution-ordered task plan and a proposed coding brief.

The synchronous `/agents/runs` graph holds state only during that request. Durable `/executions` uses PostgresSaver checkpoints, tenant-scoped execution records and explicit cancellation/resume. Repository analysis is bounded and read-only; generated changes remain proposals until approval. Sandbox tests run in networkless, nonroot containers over temporary copies. Semantic memory uses local Ollama embeddings and pgvector with both owner and project filters.

## Phase 10 approvals and GitHub

The authenticated project workbench combines repository analysis, generated change previews, sandbox results, execution history and traces, approval review, project memory and GitHub settings. A change can enter the durable approval queue only after the server runs the sandbox profile and records a passing result. Each decision is tenant-scoped and adds an audit event.

GitHub personal access tokens are validated against GitHub, encrypted with Fernet using a key held outside source control, and never returned to the browser. A user must explicitly approve a pending change before the publisher creates a unique branch and opens a pull request. Before writing, it compares the Git blob hashes of changed files with the reviewed source. It refuses stale content. It does not merge pull requests or write generated changes into local checkouts.

## Phase 11 operations and release checks

`/health` is a process liveness endpoint. `/api/v1/monitoring/ready` checks PostgreSQL and both configured Ollama models. CI runs backend lint, typing and tests on Python 3.11 and 3.13; verifies the database-backed approval flow; runs Firebase Auth and Firestore emulator tests; builds the frontend; and checks Python and npm dependencies for known vulnerabilities.

Approval source and test output are persisted in PostgreSQL, which must be protected and backed up. Local development uses Firebase emulators. A Firebase-only preview is published at <https://agent-swe-20261109-8f03.web.app>; it provides the frontend and Firebase sign-in, but not agent runs or code testing. A full cloud deployment still requires a private model endpoint, PostgreSQL with pgvector, secret management, and a dedicated Docker sandbox host.
