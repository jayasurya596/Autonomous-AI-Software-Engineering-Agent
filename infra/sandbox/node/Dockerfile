FROM node:22-bookworm-slim

RUN groupadd --gid 65532 sandbox \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin sandbox

ENV HOME=/tmp \
    TMPDIR=/tmp

USER 65532:65532
WORKDIR /workspace
