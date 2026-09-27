# syntax=docker/dockerfile:1.7
# Image for the `web` service: Next.js standalone server.
#
# Optional build secret `extra_ca`: a PEM bundle for TLS-intercepting proxies. It is
# only mounted during downloads and never stored in the image.

ARG NODE_IMAGE=node:22-alpine

FROM ${NODE_IMAGE} AS pnpm
ENV COREPACK_HOME=/opt/corepack
RUN --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /app

FROM pnpm AS deps
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN --mount=type=secret,id=extra_ca,required=false \
    --mount=type=cache,target=/root/.local/share/pnpm/store \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    pnpm install --frozen-lockfile

FROM pnpm AS builder
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN pnpm build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
