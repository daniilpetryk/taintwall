# syntax=docker/dockerfile:1
# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
#
# Never built. Pins the tool images the Makefile and pre-commit hooks run, in a
# format Dependabot understands, so they get tag and digest updates like the
# real images. Read with: sed -n 's/^FROM \(.*\) AS <name>$/\1/p'

FROM hadolint/hadolint:v2.14.0@sha256:27086352fd5e1907ea2b934eb1023f217c5ae087992eb59fde121dce9c9ff21e AS hadolint
FROM anchore/grype:v0.122.0@sha256:013e16ae96bd64108973e3e9fecec63f7ff276358d34c46c25d33e2aac96539f AS grype
