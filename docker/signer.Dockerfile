# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# Signer: static Go binary on distroless, non-root, no shell.
# Build from the repo root: docker build -f docker/signer.Dockerfile .
# Base images are pinned by digest; Dependabot bumps tag and digest together.

# Cross-compile on the build machine's native platform instead of emulating
# the target one: fast multi-arch builds (docker buildx --platform ...).
FROM --platform=$BUILDPLATFORM golang:1.27-trixie@sha256:f18c3d73a184fb0582828510722e723b63a93f7188d11dd223d9be6f6880e65d AS build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
# go.su[m] matches go.sum once it exists and is skipped while there are no deps.
COPY signer/go.mod signer/go.su[m] ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download && go mod verify
COPY signer/ ./
ARG VERSION=0.0.0-dev
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -mod=readonly \
      -ldflags="-s -w -X github.com/daniilpetryk/taintwall/signer/internal/buildinfo.Version=${VERSION}" \
      -o /out/signer ./cmd/signer

FROM gcr.io/distroless/static-debian13:nonroot@sha256:aafe993665bf6200db38a02c11a9351e782a84b6a45e7f33b6695638c88e3931
LABEL org.opencontainers.image.title="taintwall-signer" \
      org.opencontainers.image.source="https://github.com/daniilpetryk/taintwall" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=build /out/signer /signer
USER nonroot:nonroot
ENTRYPOINT ["/signer"]
