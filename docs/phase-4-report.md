# Phase 4 API middleware and authorization report

## Status

Complete against the local Auth and Standard Firestore emulators. The phase adds shared API protections around the authenticated profile, workspace and project endpoints. No cloud project was created or modified.

## Implementation

- Added configurable explicit-origin CORS, validated through `CORS_ORIGINS`.
- Added a UUID request ID to every response, accepted a valid caller-supplied UUID, and included the ID in compact structured request logs. Logs omit paths, request bodies, tokens, user IDs and query values.
- Added security response headers, `Cache-Control: no-store` on API responses, safe HTTP and validation error envelopes, and a generic 500 response that does not echo exception details.
- Added a configurable request-body limit (1 MiB default, 10 MiB maximum) and a configurable sliding-window rate limit (120 requests per 60 seconds by default).
- Added `/api/v1/monitoring/health` without exposing dependency configuration.
- Required a validated `Idempotency-Key` for workspace and project creation. Firestore transactions scope the key to the authenticated owner and operation, replay the original resource for a matching request, and return 409 when a key is reused with a different payload. Key records store only hashes and a resource pointer and expire after 24 hours.
- Updated the frontend to send a stable key for an unchanged create request, including a retry after a lost response.

## Verification

| Check | Result |
| --- | --- |
| Auth and Standard Firestore emulators | Started locally on `127.0.0.1:9099` and `127.0.0.1:8080` |
| Emulator-backed suite | `uv run pytest -q` with both emulator hosts configured: 16 passed |
| Authorization and idempotency | Owner isolation, identical-request replay, different-payload conflict, expired-key reuse, and rejection of replay after resource deletion passed |
| Middleware | Request IDs, explicit CORS, oversized body rejection, rate-limit rejection, safe errors and response security headers passed |
| Python lint | `uv run ruff check backend`: passed |
| Python typing | `uv run mypy`: passed; 21 source files checked |
| Frontend production build | `cd frontend; npm run build`: passed |
| Firebase configuration files | `firebase.json` and `firestore.indexes.json` parse as valid JSON |

The emulator run used the demo project only. It did not verify Firebase Cloud TTL policy activation or distributed rate limiting.

## Limits to address before multi-worker deployment

The sliding-window limiter keeps counters in process memory and uses the immediate client address. It is suitable for local development and a single API process; it does not coordinate limits across workers and does not trust forwarded-address headers. A scaled deployment needs a shared limiter and explicitly trusted proxy configuration.

The 24-hour idempotency expiry is enforced by the API and included in `firestore.indexes.json` as a TTL policy, but that policy is not active in the local emulator and has not been deployed. The API treats an expired key as reusable even if its backing record remains.

## Next phase

Phase 5 adds Ollama connectivity and LangGraph orchestration. It has not started.
