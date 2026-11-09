#!/bin/sh
set -eu

mkdir -p /app/repositories /var/lib/docker /run

dockerd-entrypoint.sh dockerd \
  --iptables=false \
  --ip6tables=false \
  --ip-forward=false \
  --bridge=none &
dockerd_pid=$!
trap 'kill "$dockerd_pid" 2>/dev/null || true' EXIT INT TERM

attempt=0
until docker version >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 180 ]; then
    echo "Docker did not become ready." >&2
    exit 1
  fi
  sleep 1
done

docker build --network=host \
  --file /app/infra/sandbox/python/Dockerfile \
  --tag "${SANDBOX_PYTHON_IMAGE:-agent-sandbox-python:cloudflare}" \
  /app
docker build --network=host \
  --file /app/infra/sandbox/node/Dockerfile \
  --tag "${SANDBOX_NODE_IMAGE:-agent-sandbox-node:cloudflare}" \
  /app

exec uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port 8000
