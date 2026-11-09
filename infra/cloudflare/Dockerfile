FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.13-alpine AS python313

FROM docker:dind

RUN apk add --no-cache \
      ca-certificates \
      libffi \
      openssl \
      postgresql-libs \
      tar

COPY --from=python313 /usr/local/ /usr/local/

COPY --from=uv /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    DOCKER_TLS_CERTDIR="" \
    DOCKER_HOST=unix:///var/run/docker.sock \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TMPDIR=/tmp

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN apk add --no-cache --virtual .build-deps \
      build-base \
      cargo \
      libffi-dev \
      linux-headers \
      openssl-dev \
      postgresql-dev \
    && uv sync --frozen --python /usr/local/bin/python3.13 --no-dev --no-install-project \
      --extra agents --extra firebase --extra database --extra git \
    && apk del .build-deps

COPY backend ./backend
COPY infra/sandbox ./infra/sandbox
COPY infra/cloudflare/entrypoint.sh /usr/local/bin/agent-entrypoint.sh
RUN chmod 0755 /usr/local/bin/agent-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/agent-entrypoint.sh"]
