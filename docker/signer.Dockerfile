# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# Signer: static Go binary on distroless, non-root, no shell.
# Build from the repo root: docker build -f docker/signer.Dockerfile .

FROM golang:1.27-trixie AS build
WORKDIR /src
COPY signer/go.mod ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY signer/ ./
ARG VERSION=0.0.0-dev
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath \
      -ldflags="-s -w -X github.com/daniilpetryk/taintwall/signer/internal/buildinfo.Version=${VERSION}" \
      -o /out/signer ./cmd/signer

FROM gcr.io/distroless/static-debian13:nonroot
LABEL org.opencontainers.image.title="taintwall-signer" \
      org.opencontainers.image.source="https://github.com/daniilpetryk/taintwall" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=build /out/signer /signer
USER nonroot:nonroot
ENTRYPOINT ["/signer"]
