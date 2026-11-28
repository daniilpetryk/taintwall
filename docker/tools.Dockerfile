# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# Never built. Pins the tool images the Makefile and pre-commit hooks run, in a
# format Dependabot understands, so they get tag and digest updates like the
# real images. Read with: sed -n 's/^FROM \(.*\) AS <name>$/\1/p'

FROM hadolint/hadolint:v2.15.1@sha256:32dac94127fd60b7b7e3fbfc65e1383b9b5e25c9bfd7b8536de7a539fe68a12d AS hadolint
FROM anchore/grype:v0.122.1@sha256:661583704a253c0448ed63998b5ea2cd9d5a76ee71eedc4aed439b8ce3e93a1c AS grype
