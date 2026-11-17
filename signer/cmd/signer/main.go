// Copyright 2026 Daniil Petryk
// SPDX-License-Identifier: Apache-2.0

// Command signer is taintwall's isolated signing service. It is the only
// process that holds the private key. Stub until stage 5.
package main

import (
	"fmt"
	"os"

	"github.com/daniilpetryk/taintwall/signer/internal/buildinfo"
)

func main() {
	fmt.Fprintf(os.Stderr, "taintwall signer %s: not implemented yet (stage 5)\n", buildinfo.Version)
	os.Exit(1)
}
