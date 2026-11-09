# Phase 3 Firestore and project management report

## Status

Complete against the local Standard Firestore Emulator. The user selected emulator-only development because no Firebase project exists yet. No cloud Firestore database was created or accessed.

## Implementation

- Added Firestore data models and validated inputs for onboarding profiles, workspaces and software projects.
- Added authenticated profile, workspace and project endpoints under `/api/v1`. Workspace and project queries are owner-scoped, project creation verifies workspace ownership, cursors are bounded, and non-owner access returns not found.
- Updated onboarding to save and load profile preferences through the API. Added dashboard controls for creating workspaces and projects and viewing workspace-scoped project lists.
- Refreshes the Firebase ID token after email verification so the API receives the updated verified-email claim.
- Added Firestore rules that allow authenticated owners to read their own valid profile, workspace and project documents. All client writes and unrecognized paths are denied. Backend operations use the Admin SDK and enforce ownership in the repository layer.
- Added the composite index used by workspace-scoped project lists and configured the emulator for the Standard edition.
- Added `scripts/start-firebase-emulators.ps1`; it starts Auth and Firestore together.

## Verification

| Check | Result |
| --- | --- |
| Firebase Auth and Standard Firestore emulator startup | Passed on `127.0.0.1:9099` and `127.0.0.1:8080` |
| Emulator integration suite | `uv run pytest -q` with both emulator hosts set: 11 passed |
| Tenant and persistence checks | Profile persistence, workspace pagination, workspace-scoped project listing, ownership enforcement and project deletion passed |
| Firestore Security Rules REST checks | Owner reads for profiles, workspaces and projects passed; non-owner and anonymous reads, plus owner client delete, were denied |
| Browser end-to-end flow | Registered and verified a demo account, saved onboarding preferences, created a workspace and project, then reloaded and confirmed the saved data returned |
| Browser console | No errors during the successful end-to-end flow |
| Python lint | `uv run ruff check backend`: passed |
| Python typing | `uv run mypy`: passed; 18 source files checked |
| Frontend production build | `cd frontend; npm run build`: passed |
| Firebase configuration files | `firebase.json` and `firestore.indexes.json` parse as valid JSON |

To rerun emulator-backed tests in PowerShell after starting the emulators:

```powershell
$env:APP_ENV = "test"
$env:FIREBASE_PROJECT_ID = "demo-software-agent"
$env:FIREBASE_AUTH_EMULATOR_HOST = "127.0.0.1:9099"
$env:FIRESTORE_EMULATOR_HOST = "127.0.0.1:8080"
uv run pytest -q
```

Firestore emulator data is temporary and is cleared when the emulator stops. The demo project ID is local only. Live Firebase credentials, cloud deployment and Google sign-in were not tested.

## Security rules review

Prototype Security Rules validate allowed fields and stored types for documents read by clients. They scope reads to the document owner, verify that a project belongs to the owner's workspace, and deny client mutations. The FastAPI service uses the Admin SDK, which bypasses Firestore rules; its ownership checks remain essential. Review and verify the rules against the production project's exact data model before broad sharing.

## Next phase

Phase 4 covers API middleware and authorization hardening. It has not started; this build stops at the verified Phase 3 boundary.
