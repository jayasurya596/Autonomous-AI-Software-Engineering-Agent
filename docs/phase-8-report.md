# Phase 8 verification report

## Delivered

- Added local Python and Node sandbox images. Both run as UID/GID 65532.
- Added an authenticated test endpoint that applies proposed changes only to a temporary copy and runs one fixed profile: `python -m pytest -q -p no:cacheprovider` or `node --test`.
- Containers use `--network none`, drop all Linux capabilities, disallow privilege escalation, run with a read-only root and read-only workspace, and have CPU, memory, process, wall-clock and output limits. Only `/tmp` is writable. No Docker socket or source checkout is mounted.
- Temporary workspaces omit credentials, Git internals, dependency/build folders and symlinks, and are removed after execution.

## Verification

- Built `agent-sandbox-python:local` and `agent-sandbox-node:local` successfully from the checked-in Dockerfiles.
- `uv run pytest -q`: 41 passed, 7 skipped (opt-in model and Docker checks are skipped by default).
- `RUN_DOCKER_LIVE_TESTS=1 uv run pytest backend/tests/test_sandbox_live.py -q`: 1 passed; the API ran real Python and Node tests in containers, applied a proposed fix only in scratch, and confirmed the source checkout stayed unchanged.
- `uv run ruff check backend`: passed.
- `uv run mypy`: passed for 30 application files.

## Local setup

```powershell
docker build -t agent-sandbox-python:local -f infra/sandbox/python/Dockerfile .
docker build -t agent-sandbox-node:local -f infra/sandbox/node/Dockerfile .
```

## Limitations

The fixed profiles cover Python pytest and Node's built-in test runner; they do not install project dependencies or allow arbitrary test commands. Tests requiring third-party packages need an explicitly reviewed image/profile extension. Containers have no network, so they cannot fetch packages or call external services.
