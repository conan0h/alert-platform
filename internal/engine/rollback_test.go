package engine

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/conanohara/alert-platform/internal/audit"
	pexec "github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

// This test is the platform's thesis, executable: a deploy that fails
// mid-flight is rolled back automatically, and both the failure and the
// rollback are on the record before Apply returns.
//
// It drives the real Apply loop — preflight gates included — against a
// throwaway copy of the repo (so tag checks have real tags to find) and a
// runner that reports one command as failed. Nothing is mocked out of the
// path an operator would actually hit.

// failOnce wraps a runner and fails the first command containing `match`.
// The same command must succeed when the rollback re-issues it, which is
// exactly the shape of a transient deploy failure.
type failOnce struct {
	inner pexec.Runner
	match string
	fired bool
}

func (f *failOnce) Run(cmd string) (pexec.Result, error) {
	if !f.fired && strings.Contains(cmd, f.match) {
		f.fired = true
		return pexec.Result{Command: cmd, ExitCode: 1, Stderr: "injected failure: unit refused to restart"}, nil
	}
	return f.inner.Run(cmd)
}

func (f *failOnce) WriteFile(path, content string, mode os.FileMode) error {
	return f.inner.WriteFile(path, content, mode)
}

func (f *failOnce) Describe() string { return f.inner.Describe() }

// testRepo copies fleet/, schema/ and tools/ into a temp dir and makes it a
// git repo with the tags the specs reference, so `checkRefExists` — gate 3 —
// runs for real instead of being skipped.
func testRepo(t *testing.T, tags ...string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not available")
	}
	if err := exec.Command("python3", "-c", "import yaml, jsonschema").Run(); err != nil {
		t.Skip("validator deps (pyyaml, jsonschema) not available")
	}

	src := repoRoot(t)
	dst := t.TempDir()
	for _, d := range []string{"fleet", "schema", "tools"} {
		if out, err := exec.Command("cp", "-r", filepath.Join(src, d), dst).CombinedOutput(); err != nil {
			t.Fatalf("copy %s: %v: %s", d, err, out)
		}
	}
	git := func(args ...string) {
		t.Helper()
		cmd := exec.Command("git", append([]string{
			"-c", "user.email=test@example.invalid", "-c", "user.name=test",
		}, args...)...)
		cmd.Dir = dst
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v: %s", args, err, out)
		}
	}
	git("init", "-q")
	git("add", ".")
	git("commit", "-q", "-m", "test fixture")
	for _, tag := range tags {
		git("tag", tag)
	}
	return dst
}

// stubObserved primes a dry runner so Observe reports a deployed, active
// service at prevRef whose unit no longer matches desired state — i.e. an
// ordinary `update`, the only action that is eligible for rollback.
func stubObserved(runner *pexec.DryRunner, service, prevRef string) {
	manifest, _ := json.Marshal(Manifest{
		Service: service, Ref: prevRef, UnitHash: "deadbeefdeadbeef", EnvHash: "deadbeefdeadbeef",
	})
	runner.Responses[fmt.Sprintf("cat %s 2>/dev/null || true", ManifestPath(service))] =
		pexec.Result{Stdout: string(manifest)}
	runner.Responses[fmt.Sprintf("sha256sum %s 2>/dev/null | cut -c1-16 || true", UnitPath(service))] =
		pexec.Result{Stdout: "deadbeefdeadbeef\n"}
	runner.Responses[fmt.Sprintf(
		"systemctl show %s --property=ActiveState,UnitFileState --value 2>/dev/null || true",
		UnitName(service))] = pexec.Result{Stdout: "active\nenabled\n"}
}

func TestFailedDeployRollsBackAutomaticallyAndAuditsBothOutcomes(t *testing.T) {
	const (
		service = "edgar-mna"
		prevRef = "v0.0.9"
		nextRef = "v0.1.0" // what the spec pins
	)

	root := testRepo(t, prevRef, nextRef)
	repo, err := fleet.Load(root)
	if err != nil {
		t.Fatal(err)
	}
	target, _ := repo.DefaultTarget()

	dry := pexec.NewDry(nil)
	stubObserved(dry, service, prevRef)
	runner := &failOnce{inner: dry, match: "systemctl restart"}

	plan, err := BuildPlan(repo, runner, []string{service})
	if err != nil {
		t.Fatal(err)
	}
	if got := plan.Services[0].Action; got != "update" {
		t.Fatalf("fixture should produce an update (rollback-eligible), got %q", got)
	}

	auditFile := filepath.Join(t.TempDir(), "audit.jsonl")
	log, _ := audit.Open(auditFile)

	applier := &Applier{
		Repo: repo, Runner: runner, Resolver: StubResolver{Value: "stub"},
		Audit: log, Target: target,
		// SkipGates keeps the post-deploy health probe (and its startup
		// grace sleep) out of a unit test; the failure is injected at the
		// restart step, which exercises the identical rollback path.
		Opts: Options{AutoApprove: true, SkipGates: true, Out: io.Discard},
	}

	err = applier.Apply(plan)
	if err == nil {
		t.Fatal("Apply must surface the deploy failure even after a clean rollback")
	}
	if !strings.Contains(err.Error(), "rolled back to "+prevRef) {
		t.Fatalf("error should tell the operator where the service ended up, got: %v", err)
	}

	// The host must have been driven back to the previous release: the
	// rollback re-fetches prevRef and re-issues the restart.
	joined := strings.Join(dry.Commands, "\n")
	if !strings.Contains(joined, "--branch "+nextRef) {
		t.Error("forward deploy never fetched the desired ref")
	}
	if !strings.Contains(joined, "--branch "+prevRef) {
		t.Error("rollback never fetched the previous ref — the service was left on the failed release")
	}
	if strings.Count(joined, "systemctl restart") < 1 {
		t.Error("rollback never restarted the service onto the previous release")
	}

	// Both outcomes must be on the record: the failed apply and the
	// automatic rollback. An incident review reads this file first.
	entries, err := log.History(service)
	if err != nil {
		t.Fatal(err)
	}
	var applied, rolledBack *audit.Entry
	for i := range entries {
		e := &entries[i]
		switch e.Event {
		case "apply":
			applied = e
		case "rollback":
			rolledBack = e
		}
	}
	if applied == nil || applied.Outcome != "failed" {
		t.Fatalf("audit log must record the failed apply; got %+v", entries)
	}
	if applied.FromRef != prevRef || applied.ToRef != nextRef {
		t.Errorf("failed apply recorded wrong refs: %s -> %s", applied.FromRef, applied.ToRef)
	}
	if rolledBack == nil || rolledBack.Outcome != "success" {
		t.Fatalf("audit log must record the rollback; got %+v", entries)
	}
	if rolledBack.FromRef != nextRef || rolledBack.ToRef != prevRef {
		t.Errorf("rollback recorded wrong refs: %s -> %s", rolledBack.FromRef, rolledBack.ToRef)
	}
	if auto, _ := rolledBack.Detail["automatic"].(bool); !auto {
		t.Error("an operator reading the log must be able to tell this rollback was automatic")
	}
}
