package exec

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The dry runner is a safety promise: `alertctl apply --dry-run` must not
// change anything. These tests exist because the first implementation broke
// that promise on a command shaped like `test -d X || sudo git clone ...`,
// which passed a naive read-only prefix check and then mutated the host.
func TestIsReadOnlyRejectsChainedMutations(t *testing.T) {
	mutating := []string{
		"test -d /opt/x || sudo -u svc git clone --depth 1 https://example.com /opt/x",
		"cat /etc/passwd && rm -rf /tmp/x",
		"ls /opt; sudo systemctl restart alert-x.service",
		"sudo systemctl daemon-reload",
		"cat /tmp/a > /tmp/b",
		"systemctl show x | xargs rm",
	}
	for _, cmd := range mutating {
		if isReadOnly(cmd) {
			t.Errorf("dry run would have executed a mutating command: %q", cmd)
		}
	}
}

func TestIsReadOnlyAllowsGenuineProbes(t *testing.T) {
	probes := []string{
		"systemctl show alert-x.service --property=ActiveState --value",
		"systemctl is-active alert-x.service",
		"cat /opt/alert-platform/x/deployed.json",
		"curl -fsS --max-time 5 http://127.0.0.1:9101/healthz",
	}
	for _, cmd := range probes {
		if !isReadOnly(cmd) {
			t.Errorf("probe should be allowed to run during a dry run: %q", cmd)
		}
	}
}

func TestDryRunnerRecordsWithoutExecuting(t *testing.T) {
	dir := t.TempDir()
	canary := filepath.Join(dir, "canary")

	d := NewDry(NewLocal(""))
	res, err := d.Run("touch " + canary)
	if err != nil {
		t.Fatal(err)
	}
	if !res.OK() {
		t.Errorf("dry run should report success without acting, got exit %d", res.ExitCode)
	}
	if _, err := os.Stat(canary); err == nil {
		t.Error("dry run executed a mutating command")
	}
	if len(d.Commands) != 1 || !strings.Contains(d.Commands[0], "touch") {
		t.Errorf("command not recorded: %v", d.Commands)
	}
}

func TestDryRunnerCapturesFilesInsteadOfWritingThem(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "unit.service")

	d := NewDry(nil)
	if err := d.WriteFile(target, "[Unit]\n", 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(target); err == nil {
		t.Error("dry run wrote a file to disk")
	}
	if d.Files[target] != "[Unit]\n" {
		t.Errorf("file content not captured for inspection: %q", d.Files[target])
	}
}

func TestLocalRunnerRootPrefixesPaths(t *testing.T) {
	root := t.TempDir()
	l := NewLocal(root)

	if err := l.WriteFile("/etc/alert-platform/x.env", "A=1\n", 0o640); err != nil {
		t.Fatal(err)
	}
	written := filepath.Join(root, "etc/alert-platform/x.env")
	raw, err := os.ReadFile(written)
	if err != nil {
		t.Fatalf("file not written under the root prefix: %v", err)
	}
	if string(raw) != "A=1\n" {
		t.Errorf("content = %q", raw)
	}
}

func TestLocalRunnerReturnsExitCodeNotError(t *testing.T) {
	// A non-zero exit is data the engine acts on, not a transport failure.
	// Conflating the two would turn "the unit is inactive" into a crash.
	res, err := NewLocal("").Run("exit 3")
	if err != nil {
		t.Fatalf("non-zero exit should not be an error: %v", err)
	}
	if res.ExitCode != 3 {
		t.Errorf("exit code = %d, want 3", res.ExitCode)
	}
}
