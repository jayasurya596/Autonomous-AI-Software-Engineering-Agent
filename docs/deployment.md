# Deployment and operations

## Local development

Run these from the project root in separate terminals after completing the setup in the README:

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d postgres
.\scripts\start-firebase-emulators.ps1
uv run uvicorn app.main:app --app-dir backend --env-file .env --host 127.0.0.1 --port 8000
```

In a fourth terminal:

```powershell
Set-Location frontend
npm run dev
```

The dashboard is available at `http://127.0.0.1:5173`. Keep Ollama running locally with both `OLLAMA_MODEL` and `OLLAMA_EMBEDDING_MODEL` pulled. Build the restricted Python and Node sandbox images using the commands in the Phase 8 report. Use a verified Firebase Emulator account to sign in.

`GET /health` reports process liveness. `GET /api/v1/monitoring/ready` returns 200 only when PostgreSQL and both configured Ollama models are available; it returns 503 otherwise. Neither endpoint exposes host names, secrets, or connection strings.

## Production deployment requirements

The checked-in `.env.example` deliberately targets demo Firebase emulators. Do not use those values in production. Before deployment:

1. Create and verify a Firebase project with Authentication and Firestore enabled. Configure a real Firebase web app, Admin SDK credentials through a secret manager or workload identity, reviewed Firestore rules, and production indexes.
2. Set `APP_ENV=production`, remove both emulator host variables, use HTTPS, set exact `CORS_ORIGINS`, and put the API behind a TLS reverse proxy. Keep the process-local rate limiter to a single API worker until a shared limiter and trusted-proxy policy are configured.
3. Provision private PostgreSQL with the pgvector extension, TLS connections, encrypted storage and tested backups. Set `DATABASE_URL` through the deployment secret manager. PostgreSQL stores executions, traces, memories, GitHub credentials and review records.
4. Provide a private Ollama-compatible endpoint with both configured models. Set `OLLAMA_BASE_URL`, `OLLAMA_MODEL` and `OLLAMA_EMBEDDING_MODEL` using deployment configuration rather than browser-visible variables.
5. Generate and back up `GITHUB_CREDENTIALS_ENCRYPTION_KEY` in the secret manager. Losing the key prevents decryption of saved GitHub tokens. Store Firebase credentials and database passwords outside the source tree.
6. Set `AGENT_REPOSITORIES_ROOT` to a dedicated, size-limited volume that contains only repositories users intend to submit to the local model. Protect that volume with host access controls and backups appropriate to the source code.
7. Configure a dedicated Docker Engine for sandbox execution. Do not expose its remote API publicly. The API invokes Docker with fixed test commands and restrictive container flags, but the Docker daemon itself remains a privileged host boundary. For a multi-tenant public service, move sandbox execution behind a separately isolated runner service before deployment.
8. Use one API worker per instance while the in-process rate limiter and local execution queue are in use. Add central rate limiting, durable worker orchestration and monitoring before horizontally scaling.

After deployment, verify `/health` and `/api/v1/monitoring/ready`, perform an authenticated account and owner-isolation smoke test, confirm the sandbox images and restrictions, then test a GitHub change through the approval screen. Approval creates a branch and pull request only after a user action; merging remains a separate GitHub review step.

## Cloud deployment status

The dedicated Firebase project `agent-swe-20261109-8f03` has Email/Password authentication and a Standard Firestore database in `asia-south1`; Firestore rules and the required composite index are deployed. The Firebase Hosting URL <https://agent-swe-20261109-8f03.web.app> remains a frontend-only preview.

The complete application is deployed at <https://autonomous-ai-software-engineering-agent.penchalajayasuryachappidi.workers.dev>. Cloudflare Workers/Containers serve the frontend and API, Workers AI runs the agent models, R2 stores repository snapshots, and Neon PostgreSQL with pgvector stores persistent memory. The production smoke check verified authentication, Firestore workspace/project creation, a seven-agent run, vector-memory create/search/delete, R2 upload/readback, and both isolated sandbox runtimes. See [`cloudflare-deployment.md`](cloudflare-deployment.md) for the active deployment and its operating limits. GitHub branch/PR publishing still requires an individual user to connect a GitHub token and target repository.

## OCI Always Free deployment

The repository now includes a single-VM OCI deployment for the API, React frontend, PostgreSQL/pgvector, Ollama, Caddy HTTPS, and the fixed Python/Node test images. It is sized for the currently documented Always Free Ampere A1 allowance of 2 OCPUs and 12 GB RAM after the trial period. The models are configured as `llama3.2:1b` and `nomic-embed-text`; inference will be modest on this CPU-only machine. OCI's Always Free block storage allowance is 200 GB total, including the VM boot volume. Only the A1 Always Free allocation should be used for a $0 target; do not create a larger paid shape or click Upgrade. Oracle can reclaim an Always Free VM if its CPU, network, and (for A1) memory usage all remain below its idle thresholds for seven days, so this is not an uptime guarantee.

### Account and VM setup

1. Create or use your one permitted OCI Free Tier account at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/). Pick the home region near the users you expect; Always Free compute is provisioned in that home region. Oracle asks for a payment card to verify identity and says it may place a temporary authorization hold that is typically released in three to five days. The signup offer may include US$300 of trial credits for 30 days, but the deployment below does not depend on those credits.
2. Create an Ubuntu 24.04 ARM instance using shape `VM.Standard.A1.Flex`, set it to **2 OCPUs / 12 GB RAM**, and keep the boot volume at **50 GB**. Confirm the console marks the selected resources Always Free eligible. Save the SSH private key on your laptop; do not send it in chat. If the A1 shape reports no capacity, retry later in the home region.
3. Give the VM an ephemeral public IPv4 address and configure its subnet/network security rules to allow inbound TCP 22 from your own IP and TCP 80/443 from the internet. Do not open 8000, 5432, or 11434. Keep SSH restricted to your IP.
4. Install Docker Engine and the Compose plugin on Ubuntu. Add a free DNS hostname such as `<public-ip>.nip.io`, where `<public-ip>` is the VM's IPv4 address. Set `APP_DOMAIN` and `CORS_ORIGINS` to that exact HTTPS hostname in `.env.oci`. Caddy will request and renew its HTTPS certificate automatically. Add the same hostname to Firebase Authentication's authorized domains.

### Application setup

1. Copy `.env.oci.example` to `.env.oci` on the VM and fill in the Firebase web app values from the existing `agent-swe-20261109-8f03` project. Keep `VITE_FIREBASE_GOOGLE_AUTH_ENABLED=false` unless Google sign-in has been configured in Firebase Authentication.
2. Create a Firebase Admin service-account JSON key for the same Firebase project and place it at `/opt/autonomous-agent/secrets/firebase-admin.json` on the VM. Make the API's UID 10001 the owner and set mode 0400 so the non-root API can read it:

   ```bash
   sudo install -d -m 0700 /opt/autonomous-agent/secrets
   sudo chown 10001:10001 /opt/autonomous-agent/secrets/firebase-admin.json
   sudo chmod 0400 /opt/autonomous-agent/secrets/firebase-admin.json
   ```

   Do not put this file in the repository or browser build. Set `FIREBASE_ADMIN_KEY_FILE` to its absolute path.
3. Generate the PostgreSQL password with `openssl rand -hex 32`, generate `GITHUB_CREDENTIALS_ENCRYPTION_KEY` with the command in the README, and set `DOCKER_GID` to the numeric group ID of `/var/run/docker.sock`. Create `SANDBOX_SHARED_PATH`, set its owner to UID/GID 10001, and use that exact same absolute path in `.env.oci`; the API and host Docker daemon need to see the same path for test workspaces.
4. Transfer the project source to `/opt/autonomous-agent` without local `.env` files, Firebase credentials, `.venv`, `node_modules`, or Firebase emulator state. In that directory, build the API, frontend, and sandbox images with:

   ```bash
   docker compose --env-file .env.oci -f infra/oci/compose.yaml --profile image-build build api web sandbox-python sandbox-node
   ```

5. Start PostgreSQL and Ollama, pull the configured models, then start the app:

   ```bash
   docker compose --env-file .env.oci -f infra/oci/compose.yaml up -d postgres ollama
   docker compose --env-file .env.oci -f infra/oci/compose.yaml exec ollama ollama pull llama3.2:1b
   docker compose --env-file .env.oci -f infra/oci/compose.yaml exec ollama ollama pull nomic-embed-text
   docker compose --env-file .env.oci -f infra/oci/compose.yaml up -d api web caddy
   ```

6. Check `https://<APP_DOMAIN>/health` and `https://<APP_DOMAIN>/api/v1/monitoring/ready`, then sign in with a verified Firebase account. The readiness route must report both PostgreSQL and Ollama as ready. The `postgres` and `ollama` services are private to the Compose network; only Caddy publishes web ports.

The API needs access to the host Docker socket to run the fixed sandbox profiles. Docker socket access is highly privileged, so treat this as a personal/resume demo on a dedicated VM, keep software updated, and do not give untrusted users access. PostgreSQL, Ollama model data, Firebase credentials, repository checkouts, and Caddy certificates persist on the VM; back them up before replacing it. This setup has not been applied to an OCI account yet. OCI account creation, the VM's public IP, and Firebase Admin key are still required before deployment can be completed and verified against the live service.
