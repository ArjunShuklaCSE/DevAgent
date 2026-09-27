# syntax=docker/dockerfile:1.7
# Sandbox image: the only image the worker may start (see ADR 0007 and ADR 0012).
#
# The base is pinned by digest so every run records exactly what it ran on; the tools
# the agent may call are pinned in docker/sandbox-requirements.txt. Nothing in this
# image runs as root, and the worker starts it with a read-only root filesystem.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY docker/sandbox-requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt /tmp/ca.pem; \
    groupadd --gid 10001 sandbox; \
    useradd --uid 10001 --gid sandbox --home-dir /tmp --no-create-home --shell /usr/sbin/nologin sandbox

LABEL org.opencontainers.image.title="devagent-sandbox"
USER 10001:10001
WORKDIR /workspace
CMD ["python", "--version"]
