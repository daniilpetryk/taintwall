# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# Dev toolbox: Go, Python, uv and golangci-lint at the same versions as CI,
# so `make check` behaves the same on any machine. Used by `make docker-check`.
# Runs as root on purpose: it only works on a bind-mounted checkout.

FROM golangci/golangci-lint:v2.15.0 AS golangci

FROM golang:1.27-trixie
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/
COPY --from=golangci /usr/bin/golangci-lint /usr/local/bin/golangci-lint
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy
RUN uv python install 3.14 \
 && git config --system --add safe.directory /workspace
WORKDIR /workspace
CMD ["make", "check"]
