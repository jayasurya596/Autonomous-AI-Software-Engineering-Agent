# Cloudflare deployment

## Current deployment

The complete app is live at <https://autonomous-ai-software-engineering-agent.penchalajayasuryachappidi.workers.dev>. Cloudflare Workers Static Assets serves React, and `/api/*` routes through a Durable Object to a FastAPI Cloudflare Container. The container runs the restricted Docker-in-Docker test runner. Workers AI provides the chat and embedding models; R2 stores private source snapshots; Firebase provides Authentication and Firestore; Neon provides PostgreSQL with pgvector.

The current Worker version is `8882b090-e93e-4e63-b2a2-754efd81950a` (deployed 2026-11-09). On this release, the homepage returned HTTP 200 with CSP, HSTS, frame, content-type, referrer, and permissions headers; API health and readiness passed with database and model ready; verified Firebase sign-in and authenticated `/auth/me` passed; and Firestore workspace/project create, read, and ownership checks passed. Temporary Firebase and Firestore records were removed. The preceding release also passed a seven-agent AI run, Neon vector-memory create/search/delete, R2 upload/readback, and both isolated test runtimes.

The Firebase Hosting URL <https://agent-swe-20261109-8f03.web.app> is a separate frontend-only preview. Use the Cloudflare URL for the complete application.

## Deploy an update

The main deployment script builds the frontend and container image, uploads the configured Firebase and Neon values as Worker secrets, deploys, then removes its temporary secrets file. Keep `.env.cloudflare`, `.env.production.local`, and the Firebase Admin JSON private and outside source control.

From PowerShell in the project directory, with Docker Desktop running and Wrangler signed in:

```powershell
.\scripts\deploy-cloudflare.ps1
```

The script reads the Firebase web configuration from `.env.production.local` and the following runtime values from `.env.cloudflare`:

- `FIREBASE_PROJECT_ID` and `FIREBASE_ADMIN_CREDENTIALS_FILE`
- `DATABASE_URL` using `postgresql+psycopg://...` and `sslmode=require`
- `GITHUB_CREDENTIALS_ENCRYPTION_KEY`, generated with Fernet

It checks that the Firebase web and Admin SDK project IDs match. The Cloudflare account must have the `agent-swe-repositories` R2 bucket, Workers AI, and Containers enabled. The Firebase workers.dev domain is already authorized for sign-in.

After deploying, verify the site and service endpoints:

```powershell
$base = "https://autonomous-ai-software-engineering-agent.penchalajayasuryachappidi.workers.dev"
(Invoke-WebRequest -Uri $base).StatusCode
Invoke-RestMethod -Uri "$base/health"
Invoke-RestMethod -Uri "$base/api/v1/monitoring/ready"
```

The readiness response must show both `database` and `model` as `ready`. A full authenticated feature check also needs a verified Firebase user.

## GitHub connection

The deployment has the encryption key required to save GitHub credentials, but each app user still needs to connect their own GitHub token and repository in the workbench. The app runs the fixed sandbox checks and creates a branch and pull request only after the user approves a proposal. It does not merge the pull request. CI covers this lifecycle with simulated GitHub calls; publishing to a real repository remains unverified until a user connects a real account and repository.

## Cost and operating limits

- Cloudflare Workers Paid currently starts at $5 USD/month and includes limited container memory, CPU, and disk usage. The configured `standard-1` instance provides 0.5 vCPU, 4 GiB memory, and 8 GB disk; usage above plan allowances is metered. Check current [Containers pricing](https://developers.cloudflare.com/containers/platform/pricing/) before changing the instance size or inactivity timeout.
- Workers AI includes a daily free allocation, with additional usage billed at published rates. Check [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) and monitor usage in the Cloudflare dashboard.
- Worker logs are enabled with 10% request sampling and issue detection. Check [Workers Logs pricing and retention](https://developers.cloudflare.com/workers/observability/logs/workers-logs/) because those terms can change.
- Neon and Firebase have separate quotas and account settings. Review database backups/restore options, service quotas, and budget alerts in their dashboards before handling external-customer data.
- The container sleeps after five minutes without requests to limit idle usage. Its disk is ephemeral. On a cold start, the entrypoint prepares the Python and Node sandbox images, so the first API response after sleep can take substantially longer than a warm request. Repository source is kept in R2; persistent application records are in Firebase and Neon.
- The API is published on a `workers.dev` hostname. A custom domain and its DNS are not configured.

## Security model

- The Worker accepts browser API requests only from the same origin, applies defensive browser headers, and overwrites the internal client-IP header from Cloudflare's connection metadata before forwarding requests.
- The backend verifies Firebase ID tokens and requires verified email addresses. Firestore operations enforce owner checks.
- Repository ZIPs are owner-scoped in R2 and reject unsafe paths, symbolic links, and oversized archives.
- Test commands are fixed by runtime. Sandbox containers run without network access, with a read-only filesystem, dropped Linux capabilities, memory/CPU/process limits, and a 120-second timeout. The outer API container requires internet access for Firebase, Neon, and GitHub services.
- Cloudflare observability logs only a sample of requests; avoid adding prompts, credentials, or source code to logs.

For local startup, see [`deployment.md`](deployment.md). OCI configuration remains under `infra/oci/`.
