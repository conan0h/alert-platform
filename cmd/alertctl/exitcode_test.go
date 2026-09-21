package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// Exit codes are a contract, not an implementation detail: observe.yml reads
// them to tell "the fleet has drifted" from "I could not find out whether the
// fleet has drifted", and a scheduled job that cannot tell those apart either
// treats every drift as an outage or learns to ignore both.
//
// So they are tested through the built binary rather than by calling the
// command functions. cmdDrift reaches its finding code via os.Exit, which no
// in-process test can observe, and the thing worth pinning is what a caller
// sees anyway.

// binPath is the CLI, built once for this package's tests by TestMain.
//
// TestMain rather than a sync.Once around t.TempDir(): that directory belongs
// to whichever test happened to call the helper first and is removed when that
// test ends, which would leave a later test pointing at a binary that is no
// longer there.
var binPath string

func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "alertctl-exitcode")
	if err != nil {
		fmt.Fprintf(os.Stderr, "creating temp dir: %v\n", err)
		os.Exit(1)
	}
	// No defer: every exit from TestMain goes through os.Exit, which does not
	// run them. The cleanup is therefore spelled out on each path.

	binPath = filepath.Join(dir, "alertctl")
	// -mod=vendor explicitly: the deps are vendored so this build needs no
	// network, and CI sets that per-command rather than in the environment,
	// so it is not inherited here.
	if out, err := exec.Command("go", "build", "-mod=vendor", "-o", binPath, ".").CombinedOutput(); err != nil {
		fmt.Fprintf(os.Stderr, "building alertctl: %v\n%s", err, out)
		os.RemoveAll(dir)
		os.Exit(1)
	}

	code := m.Run()
	os.RemoveAll(dir)
	os.Exit(code)
}

func TestExitCodes(t *testing.T) {
	// The fixture fleet, not the live one: a test about exit codes must not
	// start failing because someone rolled a release ref.
	fixture := filepath.Join("..", "..", "internal", "engine", "testdata")

	cases := []struct {
		name string
		args []string
		want int
	}{
		{"no arguments is a usage error", nil, exitUsage},
		{"an unknown command is a usage error", []string{"nonsense"}, exitUsage},
		{"help is not an error", []string{"-h"}, exitOK},

		// -target dry records commands instead of running them, so nothing
		// reads as deployed and every fixture service is pending. That is a
		// finding: drift ran fine and found drift.
		{
			"drift that finds drift is a finding, not an error",
			[]string{"drift", "-root", fixture, "-target", "dry"},
			exitFinding,
		},
		// A root with no fleet in it cannot be compared at all. Same verb,
		// same non-zero-ness, different meaning — this is the distinction the
		// whole constant block exists for.
		{
			"drift that cannot run is an error, not a finding",
			[]string{"drift", "-root", filepath.Join(t.TempDir(), "empty"), "-target", "dry"},
			exitError,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := exec.Command(binPath, tc.args...).Run()
			got := exitOK
			var ee *exec.ExitError
			if errors.As(err, &ee) {
				got = ee.ExitCode()
			} else if err != nil {
				t.Fatalf("running alertctl %v: %v", tc.args, err)
			}
			if got != tc.want {
				t.Errorf("alertctl %v exit = %d, want %d", tc.args, got, tc.want)
			}
		})
	}
}

// A finding and an error must never share a code, however the constants are
// later renumbered. This is the invariant observe.yml depends on.
func TestFindingAndErrorAreDistinguishable(t *testing.T) {
	seen := map[int]string{}
	for _, c := range []struct {
		code int
		name string
	}{
		{exitOK, "exitOK"},
		{exitError, "exitError"},
		{exitUsage, "exitUsage"},
		{exitFinding, "exitFinding"},
	} {
		if prev, dup := seen[c.code]; dup {
			t.Errorf("%s and %s are both %d; a caller cannot tell them apart", prev, c.name, c.code)
		}
		seen[c.code] = c.name
	}
}
