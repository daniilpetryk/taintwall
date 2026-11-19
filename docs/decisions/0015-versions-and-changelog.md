# 15. Semantic versions per release; the changelog is the release notes

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#28](https://github.com/daniilpetryk/taintwall/pull/28)

## Context

Releases were merge commits on `main` without names, so there was no way to say 'since 0.2.0' or read the project's evolution by version.

## Decision

Each release to `main` is tagged `vX.Y.Z`. Before 1.0 the minor version counts releases and there is no stable API. `CHANGELOG.md` holds one section per version (what it is for, what changed, what it measured); the release workflow publishes that section as the GitHub Release, so notes are written once. Past releases were tagged after the fact: v0.1.0 (design and skeleton), v0.2.0 (stage 0 and first half of stage 1), v0.3.0 (stage 1).

## Consequences

A release PR updates the changelog's Unreleased section into a version. Tags are not signed yet (no signing key in use); the merge commits they point at are.
