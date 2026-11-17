# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# taintwall Python runtime: locked dependencies only, non-root.
# Build from the repo root: docker build -f docker/app.Dockerfile .

FROM python:3.15-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, in their own layer, so code changes don't reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.15-slim-trixie
LABEL org.opencontainers.image.title="taintwall" \
      org.opencontainers.image.source="https://github.com/daniilpetryk/taintwall" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN groupadd --system --gid 10001 taintwall \
 && useradd --system --uid 10001 --gid taintwall --no-create-home taintwall
COPY --from=build --chown=taintwall:taintwall /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER taintwall
WORKDIR /app
CMD ["python", "-c", "import taintwall; print('taintwall', taintwall.__version__)"]
