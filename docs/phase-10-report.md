# Phase 10 implementation report

## Delivered

- Added a project workbench for local repository analysis, change proposals, isolated tests, agent executions and specialist traces, approval review, semantic project notes, and GitHub connection settings.
- Added a durable owner-scoped approval queue. A proposal is saved only after the API runs its own isolated sandbox test and the tests pass. The review record contains the generated files, unified diff, test profile and bounded output, source hashes, decision history, and publication result.
- Added an append-only approval event history for created, approved, rejected, published, and failed transitions. State changes lock the approval record in PostgreSQL and reject repeated decisions.
- Added GitHub personal access token verification and Fernet encryption at rest. Tokens are not returned by the API, included in logs, or sent to the language model. A private `GITHUB_CREDENTIALS_ENCRYPTION_KEY` is required.
- Added a review-gated GitHub publisher. The explicit approve action creates a new commit on a unique `codex/approval-{id}` branch and opens a pull request against the repository default branch. It does not merge the request or change the local checkout.
- Before writing, the publisher checks that each changed remote file still has the same Git blob hash as the version used for review. Stale proposals stop without creating a branch.
- Added UI actions for cancel/resume on supported executions, semantic note search and deletion, test/diff inspection, connection setup/removal, and approval/rejection.

## Verification

- `uv run ruff check backend` — passed.
- `uv run mypy` — passed across 41 application files.
- `uv run pytest backend/tests/test_github_integration.py backend/tests/test_repository_service.py -q` — 16 passed, 1 skipped. The skipped check requires Windows symlink privileges unavailable in this session.
- `RUN_PHASE10_LIVE_TESTS=1 DATABASE_URL=… uv run pytest backend/tests/test_phase10_approval_live.py -q` — passed against the local PostgreSQL service. It verified encryption at rest, test-gated approval creation, owner isolation, reject and approve transitions, event persistence, and a complete mocked GitHub pull request flow.
- `npm run build` — passed for the React/TypeScript dashboard.
- All GitHub API writes in tests used an HTTP mock; no remote repository was changed.

## Operational notes

- Set a durable private Fernet key before connecting GitHub. The local `.env` in this development checkout has a generated key and is ignored by Git. If the key is lost, saved encrypted tokens cannot be recovered.
- A GitHub token needs permission to read the repository, create contents/branches, and open pull requests. The app validates the token with `GET /user`; permission failures are reported without exposing token text.
- Approval records retain proposed source and test output in PostgreSQL. Those records are owner-scoped but are not application-layer encrypted. Use a protected local database and a production database with appropriate encryption, backups, and retention controls.
- If GitHub accepts a branch commit but refuses the subsequent pull request request, the branch may remain without a pull request. The approval is marked failed so the user can inspect the repository and create or remove that branch manually.
