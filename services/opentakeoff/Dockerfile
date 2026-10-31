# OpenTakeoff measurement sidecar for Vula's takeoff (docs/takeoff-audit.md, item 1).
#
# LICENCE PIN. OpenTakeoff's tag `apache-2.0-final` is the last commit released under Apache-2.0.
# Later upstream commits are AGPL. Vula builds ONLY from this exact commit and never merges or
# bumps past it. tests/test_opentakeoff_sidecar_pin.py fails CI if this pin changes. The upstream
# LICENSE, NOTICE and THIRD-PARTY-NOTICES are copied into the image unchanged, as Apache-2.0 §4
# requires.
#
# Build from the repo root:
#   docker build -t vula-opentakeoff services/opentakeoff
# Run (the API key locks /measure; unset = every /measure call is refused):
#   docker run -p 8790:8790 -e OPENTAKEOFF_API_KEY=<secret> vula-opentakeoff

ARG OT_REPO=https://github.com/Kentucky-ai/opentakeoff.git
ARG OT_TAG=apache-2.0-final
ARG OT_COMMIT=84c7f48e458f6cd74f694e3a970dd011c73a8f2e

# ---- fetch the pinned source and prove it is the pinned commit ---------------
FROM node:24-bookworm-slim AS source
ARG OT_REPO
ARG OT_TAG
ARG OT_COMMIT
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q . \
    && git fetch -q --depth 1 "$OT_REPO" "refs/tags/$OT_TAG:refs/tags/$OT_TAG" \
    && git checkout -q "$OT_COMMIT" \
    && test "$(git rev-parse HEAD)" = "$OT_COMMIT" \
    && test "$(git rev-parse "$OT_TAG^{commit}")" = "$OT_COMMIT" \
    && grep -q "Apache License" LICENSE \
    && grep -q '"license": "Apache-2.0"' mcp/package.json \
    && rm -rf .git

# ---- build the upstream MCP server exactly as its own Dockerfile does ---------
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY --from=source /src/ ./
WORKDIR /app/mcp
RUN npm ci && npm run build

# ---- runtime: upstream server + Vula's HTTP bridge ---------------------------
FROM node:24-bookworm-slim AS runtime
ARG OT_TAG
ARG OT_COMMIT
ENV NODE_ENV=production \
    OPENTAKEOFF_TAG=$OT_TAG \
    OPENTAKEOFF_COMMIT=$OT_COMMIT \
    PORT=8790
WORKDIR /app/mcp
COPY --from=build /app/mcp/package.json /app/mcp/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/mcp/dist ./dist
# Upstream licence files, unchanged.
COPY --from=source /src/LICENSE /src/NOTICE /src/THIRD-PARTY-NOTICES.md /app/licenses/opentakeoff/
# Vula's own code: the bridge (sits beside dist/ so it resolves the upstream SDK dependency).
COPY bridge.mjs ./bridge.mjs
USER node
EXPOSE 8790
CMD ["node", "bridge.mjs"]
