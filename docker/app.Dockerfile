# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# taintwall Python runtime: locked dependencies only, non-root, no pip.
# Build from the repo root: docker build -f docker/app.Dockerfile .
# Base images are pinned by digest; Dependabot bumps tag and digest together.

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /uvx /bin/
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

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
LABEL org.opencontainers.image.title="taintwall" \
      org.opencontainers.image.source="https://github.com/daniilpetryk/taintwall" \
      org.opencontainers.image.licenses="Apache-2.0"
# Pull Debian security fixes released after the base image, then drop pip:
# the app runs from its own venv, so the system pip is only attack surface.
# hadolint ignore=DL3008,DL3009
RUN apt-get update \
 && apt-get upgrade --yes --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall --yes --root-user-action=ignore pip \
 && groupadd --system --gid 10001 taintwall \
 && useradd --system --uid 10001 --gid taintwall --no-create-home taintwall
COPY --from=build --chown=taintwall:taintwall /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER taintwall
WORKDIR /app
CMD ["python", "-c", "import taintwall; print('taintwall', taintwall.__version__)"]
