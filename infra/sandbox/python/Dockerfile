FROM python:3.13-slim

RUN python -m pip install --no-cache-dir pytest==8.4.2 \
    && groupadd --gid 65532 sandbox \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin sandbox

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp

USER 65532:65532
WORKDIR /workspace
