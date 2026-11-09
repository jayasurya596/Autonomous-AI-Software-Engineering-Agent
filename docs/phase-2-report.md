# Phase 2 authentication report

## Status

Firebase Authentication was implemented and verified against the local demo Auth Emulator in Phase 2. The onboarding profile fields were completed with Firestore integration in Phase 3; the combined local sign-in and onboarding flow is now available. No production Firebase project or cloud data service has been created.

## Files created or updated

- `backend/app/core/auth.py` initializes Firebase Admin from a local credential file or Application Default Credentials, verifies ID tokens, rejects revoked/invalid tokens, requires verified email, and blocks emulator tokens outside development/test.
- `backend/app/api/auth.py` adds `GET /api/v1/auth/me`.
- `backend/tests/test_auth.py` covers missing credentials, missing/invalid tokens, unverified users and trusted claim projection.
- `backend/tests/test_auth_emulator.py` runs registration, email verification, sign-in and backend token verification against a running emulator.
- `frontend/src/auth/` and `frontend/src/lib/firebase.ts` add email/password and Google sign-in, password reset, verification email, session handling and onboarding display-name update.
- `frontend/src/api/client.ts` attaches the current Firebase ID token to API requests.
- `firebase.json`, `scripts/start-firebase-emulators.ps1` and `.env.example` configure a no-cloud Auth and Standard Firestore emulator workflow.

## Verification

| Check | Command or action | Result |
| --- | --- | --- |
| Frontend compile and production build | `cd frontend; npm run build` | Passed |
| Frontend production dependency audit | `cd frontend; npm audit --omit=dev` | Passed; 0 vulnerabilities |
| Python tests | `APP_ENV=test`, `FIREBASE_PROJECT_ID=demo-software-agent`, `FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099`, then `uv run pytest -q` | Passed; 8 tests |
| Live auth integration | Local demo Auth Emulator: registration → verification email action → sign-in → `/api/v1/auth/me` | Passed; backend returned the verified user identity |
| Python lint | `uv run ruff check backend` | Passed |
| Python typing | `uv run mypy` | Passed; 9 source files |

The emulator test uses a randomly generated local test account. No cloud project or live Firebase resource was created. Google popup sign-in and password reset email delivery were not exercised against a live provider.

## Run locally

1. Copy `.env.example` to `.env`; the Firebase values target the local demo emulator.
2. Install dependencies with `uv sync --extra firebase --group dev`; then run `npm install` from `frontend/`.
3. Start both Firebase emulators with `.\scripts\start-firebase-emulators.ps1`.
4. Start the API with `uv run uvicorn app.main:app --app-dir backend --env-file .env --reload`.
5. Start the frontend with `cd frontend; npm run dev`.
6. To rerun the full Python suite while the emulator is running, use:

   ```powershell
   $env:APP_ENV = "test"
   $env:FIREBASE_PROJECT_ID = "demo-software-agent"
   $env:FIREBASE_AUTH_EMULATOR_HOST = "127.0.0.1:9099"
   uv run pytest -q
   ```

The emulator needs Java 21. This working copy uses an ignored portable runtime under `.tools`; other checkouts can point `JAVA_HOME` to an installed Java 21 runtime.

Phase 3 selected the Standard Firestore Emulator for continued local development. See [`phase-3-report.md`](phase-3-report.md) for the data model, ownership behavior and integration verification.
