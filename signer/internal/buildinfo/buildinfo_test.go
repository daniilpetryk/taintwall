// Copyright 2026 Daniil Petryk
// SPDX-License-Identifier: Apache-2.0

package buildinfo

import "testing"

func TestVersionIsSet(t *testing.T) {
	t.Parallel()

	if Version == "" {
		t.Fatal("Version must not be empty")
	}
}
