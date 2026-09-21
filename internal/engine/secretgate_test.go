package engine

import (
	"errors"
	"io"
	"path/filepath"
	"strings"
	"testing"

	"github.com/conan0h/alert-platform/internal/audit"
	pexec "github.com/conan0h/alert-platform/internal/exec"
	"github.com/conan0h/alert-platform/internal/fleet"
)

// What this pins, and why it was written.
//
// On 2026-08-20 two `v0.1.2` applies of `clinical-trials` were recorded
// `failed`, each followed by a `rollback` also recorded `failed`. The service
// was nonetheless active and healthy at `v0.1.0` — the ref those rollbacks were
// restoring — which reads like the audit log lying about its own safety
// mechanism.
//
// It was not lying. Secret resolution is the first thing applyService does, and
// a failure there returns before any mutating step. So the apply changed
// nothing, the rollback ran the same code path and failed at the same point
// changing nothing, and the service stayed on the ref it had never left.
// Both `failed` entries were accurate; the inference from "healthy at the
// previous ref" to "the rollback worked" was not.
//
// This test exists so that property is checked rather than re-derived: a
// resolver failure must be non-mutating on both passes.

// refusingResolver stands in for SSM on a host with no usable credentials,
// which is what the August failure was reported to be.
type refusingResolver struct{ calls int }

func (r *refusingResolver) Describe() string { return "refusing" }

func (r *refusingResolver) Resolve([]string) (map[string]string, error) {
	r.calls++
	return nil, errors.New("ssm: AccessDeniedException: no identity-based policy allows ssm:GetParameter")
}

func TestSecretResolutionFailureMutatesNothingAndAuditsBothAttemptsFailed(t *testing.T) {
	const (
		service = "fixture-filings"
		prevRef = "v0.9.0"
		nextRef = fixtureRef
	)

	root := testRepo(t, prevRef, nextRef)
	repo, err := fleet.Load(root)
	if err != nil {
		t.Fatal(err)
	}
	target, _ := repo.DefaultTarget()

	dry := pexec.NewDry(nil)
	stubObserved(dry, service, prevRef)

	plan, err := BuildPlan(repo, dry, []string{service})
	if err != nil {
		t.Fatal(err)
	}
	if got := plan.Services[0].Action; got != "update" {
		t.Fatalf("fixture should produce an update (rollback-eligible), got %q", got)
	}

	log, _ := audit.Open(filepath.Join(t.TempDir(), "audit.jsonl"))

	resolver := &refusingResolver{}
	applier := &Applier{
		Repo: repo, Runner: dry, Resolver: resolver,
		Audit: log, Target: target,
		Opts: Options{AutoApprove: true, SkipGates: true, Out: io.Discard},
	}

	err = applier.Apply(plan)
	if err == nil {
		t.Fatal("Apply must surface a secret-resolution failure")
	}
	if !strings.Contains(err.Error(), "resolve secrets") {
		t.Fatalf("error should name the gate that refused, got: %v", err)
	}

	// Twice: once forward, once for the rollback. The second call is the
	// mechanism by which the rollback also fails, and it is the part that made
	// the August audit entries look wrong.
	if resolver.calls != 2 {
		t.Errorf("resolver called %d times, want 2 (forward deploy, then rollback)", resolver.calls)
	}

	// No mutating command may have been issued on either pass. This is the
	// property that makes the failure safe and both `failed` statuses honest.
	//
	// Read-only commands are expected and are not checked for: BuildPlan
	// observes current state, and preflight observes it again. What must be
	// absent is anything that writes.
	mutations := []string{
		"git clone",   // fetch the new release
		"-m venv",     // build its virtualenv
		"pip install", // populate it
		"install -d",  // create service directories
		"systemctl restart",
		"systemctl enable",
		"tee ",   // write the unit or the environment file
		"rm -rf", // prune old releases
	}
	for _, cmd := range dry.Commands {
		for _, m := range mutations {
			if strings.Contains(cmd, m) {
				t.Errorf("issued a mutating command despite the secret gate refusing: %q", cmd)
			}
		}
	}

	entries, err := log.History(service)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatalf("want 2 audit entries (apply, rollback), got %d: %+v", len(entries), entries)
	}
	if entries[0].Event != "apply" || entries[0].Outcome != "failed" {
		t.Errorf("first entry: want apply/failed, got %s/%s", entries[0].Event, entries[0].Outcome)
	}
	if entries[1].Event != "rollback" || entries[1].Outcome != "failed" {
		t.Errorf("second entry: want rollback/failed, got %s/%s", entries[1].Event, entries[1].Outcome)
	}
	// The rollback names the ref it was trying to restore even though it never
	// got there, which is what lets a reader reconstruct what was attempted.
	if entries[1].ToRef != prevRef {
		t.Errorf("rollback entry should record the ref it aimed at (%s), got %s", prevRef, entries[1].ToRef)
	}
}
