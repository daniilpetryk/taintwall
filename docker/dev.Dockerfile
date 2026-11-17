# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# Dev toolbox: Go, Python, uv and golangci-lint at the same versions as CI,
# so `make check` behaves the same on any machine. Used by `make docker-check`,
# which runs it as the host user so files on the bind mount stay yours.
# Base images are pinned by digest; Dependabot bumps tag and digest together.

FROM golangci/golangci-lint:v2.15.0@sha256:fbfcfd12a6f8d7f1b97b3362ca80fc2a244d46863148c73cb88e7793af96c481 AS golangci

FROM golang:1.27-trixie@sha256:f18c3d73a184fb0582828510722e723b63a93f7188d11dd223d9be6f6880e65d
COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /uvx /bin/
COPY --from=golangci /usr/bin/golangci-lint /usr/local/bin/golangci-lint
# Toolchains are installed as root and read-only for everyone.
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.14 && uv cache clean
# Caches and the venv live under world-writable dirs, created empty, so any
# host uid can use them through named volumes.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/cache/uv \
    UV_LINK_MODE=copy \
    GOMODCACHE=/cache/go-mod \
    GOCACHE=/cache/go-build \
    GOLANGCI_LINT_CACHE=/cache/golangci-lint \
    HOME=/tmp
RUN mkdir -p /opt/venv /cache/uv /cache/go-mod /cache/go-build /cache/golangci-lint \
 && chmod 1777 /opt/venv /cache /cache/uv /cache/go-mod /cache/go-build /cache/golangci-lint \
 && git config --system --add safe.directory /workspace
WORKDIR /workspace
CMD ["make", "check"]
