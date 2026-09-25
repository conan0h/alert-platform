package main

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Every read verb says which binary answered. The value is whatever the
// toolchain stamped — a builder without git records nothing, and that is a
// legitimate answer — so what is pinned here is that the line is present and
// findable, which is what ssm-run and a reader both depend on.
//
// Driven through the built binary, like the exit codes: a stamp read from the
// test binary would describe the test binary, which is not the thing shipped.
func TestReadVerbsReportTheControlPlane(t *testing.T) {
	fixture := filepath.Join("..", "..", "internal", "engine", "testdata")

	for _, verb := range []string{"status", "drift"} {
		t.Run(verb, func(t *testing.T) {
			// drift against a dry target finds drift and exits 3, so the
			// error is expected and the output is what matters.
			out, _ := exec.Command(binPath, verb, "-root", fixture, "-target", "dry").Output()
			first, _, _ := strings.Cut(string(out), "\n")
			if !strings.HasPrefix(first, "control plane: alertctl ") {
				t.Fatalf("%s first line = %q, want the control-plane stamp", verb, first)
			}
		})
	}
}

func TestStatusJSONCarriesTheControlPlane(t *testing.T) {
	fixture := filepath.Join("..", "..", "internal", "engine", "testdata")
	out, err := exec.Command(binPath, "status", "-root", fixture, "-target", "dry", "-json").Output()
	if err != nil {
		t.Fatalf("status -json: %v", err)
	}

	var got struct {
		ControlPlane struct {
			Revision    string `json:"revision"`
			CommittedAt string `json:"committed_at"`
			Modified    bool   `json:"modified"`
		} `json:"control_plane"`
		Services []struct {
			Service string `json:"service"`
		} `json:"services"`
	}
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("status -json is not the documented object: %v\n%s", err, out)
	}
	if len(got.Services) == 0 {
		t.Fatalf("status -json listed no services:\n%s", out)
	}
	// The plain text prints a truncated revision; JSON is for a caller that
	// wants to compare, so it must carry the whole one.
	if r := got.ControlPlane.Revision; r != "" && len(r) != 40 {
		t.Fatalf("control_plane.revision = %q, want a full sha or nothing", r)
	}
}
