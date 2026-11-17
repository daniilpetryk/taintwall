// Copyright 2026 Daniil Petryk
// SPDX-License-Identifier: Apache-2.0

// Package buildinfo holds version information for the signer.
package buildinfo

// Version is the signer version. Release builds override it with
// -ldflags "-X github.com/daniilpetryk/taintwall/signer/internal/buildinfo.Version=...".
var Version = "0.0.0-dev"
