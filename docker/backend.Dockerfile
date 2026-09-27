# syntax=docker/dockerfile:1.7
# Image for the `api` and `worker` services (same code, different command).
#
# Optional build secret `extra_ca`: a PEM bundle for TLS-intercepting proxies. It is
# only mounted during dependency download and never stored in the image.

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=extra_ca,required=false \
    --mount=type=cache,target=/root/.cache \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export SSL_CERT_FILE=/tmp/ca.pem PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-cache-dir uv==0.8.17; \
    uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY agent ./agent
COPY tools ./tools
COPY sandbox ./sandbox
COPY llm ./llm
COPY evaluation ./evaluation
COPY database ./database
COPY mcp_server ./mcp_server
RUN --mount=type=secret,id=extra_ca,required=false \
    --mount=type=cache,target=/root/.cache \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export SSL_CERT_FILE=/tmp/ca.pem; \
    fi; \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN groupadd --system --gid 10001 devagent \
    && useradd --system --uid 10001 --gid devagent --no-create-home devagent
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
USER devagent
EXPOSE 8000
CMD ["uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
