package engine

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"github.com/conanohara/alert-platform/internal/audit"
	pexec "github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

// Options controls one apply run.
type Options struct {
	DryRun      bool
	AutoApprove bool
	// SkipGates exists for break-glass only. It is recorded in the audit log
	// precisely because it is the thing you want to find when explaining an
	// outage three weeks later.
	SkipGates  bool
	NoRollback bool
	Out        io.Writer
}

// Applier reconciles observed state to desired state.
type Applier struct {
	Repo     *fleet.Repo
	Runner   pexec.Runner
	Resolver Resolver
	Audit    *audit.Log
	Target   fleet.Target
	Opts     Options
}

func (a *Applier) logf(format string, args ...any) {
	if a.Opts.Out == nil {
		return
	}
	fmt.Fprintf(a.Opts.Out, format+"\n", args...)
}

// Apply executes a plan, one service at a time.
//
// Concurrency is capped at change_policy.max_concurrent_deploys, which is 1
// and is not a parameter here: sequential deploys mean a bad change hits one
// service, gets caught by its gate, and stops. That is the entire safety
// argument, and making it configurable per-run would let it be argued away
// at 2am.
func (a *Applier) Apply(plan Plan) error {
	if err := a.preflight(plan); err != nil {
		return err
	}

	pending := plan.Pending()
	if len(pending) == 0 {
		a.logf("Nothing to do: every service already matches desired state.")
		return nil
	}
	SortServices(pending)

	for i, sp := range pending {
		a.logf("\n[%d/%d] %s: %s %s -> %s",
			i+1, len(pending), sp.Service, sp.Action,
			orNone(sp.CurrentRef), sp.DesiredRef)

		start := time.Now()
		err := a.applyService(plan, sp)
		entry := audit.Entry{
			Event:    "apply",
			Service:  sp.Service,
			PlanID:   plan.ID,
			Actor:    plan.CreatedBy,
			Target:   a.Runner.Describe(),
			FromRef:  sp.CurrentRef,
			ToRef:    sp.DesiredRef,
			Duration: time.Since(start).Round(time.Millisecond).String(),
			Detail: map[string]any{
				"action":        sp.Action,
				"unit_hash":     sp.UnitHash,
				"env_hash":      sp.EnvHash,
				"secret_names":  sp.SecretNames,
				"dry_run":       a.Opts.DryRun,
				"gates_skipped": a.Opts.SkipGates,
			},
		}

		if err != nil {
			entry.Outcome = "failed"
			entry.Detail["error"] = err.Error()
			_ = a.Audit.Append(entry)

			if a.Opts.NoRollback || a.Opts.DryRun || sp.Action == "create" {
				return fmt.Errorf("%s: %w", sp.Service, err)
			}
			a.logf("  ! deploy failed: %v", err)
			a.logf("  > rolling back to %s", sp.CurrentRef)
			if rbErr := a.rollbackTo(plan, sp, sp.CurrentRef); rbErr != nil {
				return fmt.Errorf(
					"%s: deploy failed (%v) AND rollback failed (%v) — service may be down, "+
						"see docs/runbooks/service-down.md", sp.Service, err, rbErr)
			}
			return fmt.Errorf("%s: deploy failed and was rolled back to %s: %w",
				sp.Service, sp.CurrentRef, err)
		}

		entry.Outcome = "success"
		if err := a.Audit.Append(entry); err != nil {
			return fmt.Errorf("deploy of %s succeeded but the audit write failed: %w",
				sp.Service, err)
		}
		a.logf("  ✓ %s healthy at %s", sp.Service, sp.DesiredRef)
	}

	a.logf("\nApplied %d change(s).", len(pending))
	return nil
}

// preflight runs the checks that must pass before anything is touched.
func (a *Applier) preflight(plan Plan) error {
	policy := a.Repo.Fleet.ChangePolicy

	if policy.RequirePlanBeforeApply && plan.ID == "" {
		return fmt.Errorf("change_policy.require_plan_before_apply is set; run `alertctl plan` first")
	}

	// Gate 1: the fleet must be valid. Same entry point as local validation
	// and CI (docs/spec.md, D6) rather than a reimplementation that can drift.
	a.logf("Gate: validating fleet spec")
	if err := RunValidator(a.Repo.Root); err != nil {
		return fmt.Errorf("validation gate failed: %w", err)
	}

	// Gate 2: the plan must still describe the current specs. Re-planning
	// and comparing fingerprints is what stops a stale approval being
	// applied against edited desired state.
	//
	// The re-plan must cover exactly the services the plan covers. Planning
	// the whole fleet here would produce a different fingerprint for any
	// `-service`-scoped plan and reject every one of them as stale.
	scope := make([]string, 0, len(plan.Services))
	for _, sp := range plan.Services {
		scope = append(scope, sp.Service)
	}
	current, err := BuildPlan(a.Repo, a.Runner, scope)
	if err != nil {
		return fmt.Errorf("re-plan for freshness check: %w", err)
	}
	if current.ID != plan.ID {
		return fmt.Errorf(
			"plan %s is stale (specs or host state changed; current plan is %s). "+
				"Re-run `alertctl plan` and review the new change set", plan.ID, current.ID)
	}

	// Gate 3: every referenced tag must exist and be immutable.
	for _, sp := range plan.Pending() {
		if err := checkRefExists(a.Repo.Root, sp.DesiredRef); err != nil {
			return fmt.Errorf("%s: %w", sp.Service, err)
		}
	}

	if policy.MaxConcurrentDeploys > 1 {
		a.logf("note: max_concurrent_deploys=%d, but apply is sequential by design",
			policy.MaxConcurrentDeploys)
	}
	return nil
}

// applyService is the reconcile loop for one service.
func (a *Applier) applyService(plan Plan, sp ServicePlan) error {
	eff := sp.Effective
	name := sp.Service
	ref := sp.DesiredRef
	release := ReleaseDir(name, ref)

	secrets, err := a.Resolver.Resolve(sp.SecretNames)
	if err != nil {
		return fmt.Errorf("resolve secrets: %w", err)
	}

	steps := []struct {
		desc string
		cmd  string
	}{
		{"prepare directories", fmt.Sprintf(
			"sudo install -d -o %s -g %s -m 0755 %s %s %s && sudo install -d -m 0750 -o %s -g %s %s",
			eff.String("runtime.user", "svc-alerts"), eff.String("runtime.user", "svc-alerts"),
			ServiceRoot(name), ServiceRoot(name)+"/releases", EnvDir,
			eff.String("runtime.user", "svc-alerts"), eff.String("runtime.user", "svc-alerts"),
			eff.String("state.dir", "/var/lib/alert-platform")+"/"+name)},

		{"fetch code at pinned tag", fmt.Sprintf(
			"test -d %s || sudo -u %s git clone --depth 1 --branch %s %s %s",
			release, eff.String("runtime.user", "svc-alerts"), ref,
			eff.String("source.repo", a.Repo.Fleet.Metadata.Repo), release)},

		{"verify checkout matches the tag", fmt.Sprintf(
			"test \"$(sudo -u %s git -C %s describe --tags --exact-match 2>/dev/null)\" = %q",
			eff.String("runtime.user", "svc-alerts"), release, ref)},

		{"build virtualenv", fmt.Sprintf(
			"sudo -u %s %s -m venv %s/venv && sudo -u %s %s/venv/bin/pip install --quiet --upgrade pip && "+
				"sudo -u %s %s/venv/bin/pip install --quiet -r %s/services/requirements.txt",
			eff.String("runtime.user", "svc-alerts"), eff.String("runtime.interpreter", "python3.12"),
			release, eff.String("runtime.user", "svc-alerts"), release,
			eff.String("runtime.user", "svc-alerts"), release, release)},
	}

	for _, step := range steps {
		a.logf("  - %s", step.desc)
		res, err := a.Runner.Run(step.cmd)
		if err != nil {
			return fmt.Errorf("%s: %w", step.desc, err)
		}
		if !res.OK() {
			return fmt.Errorf("%s: exit %d: %s", step.desc, res.ExitCode,
				strings.TrimSpace(firstNonEmpty(res.Stderr, res.Stdout)))
		}
	}

	// Environment file: 0640 and owned by the service user. This is the only
	// place on the host where secret values land.
	a.logf("  - write environment (%d secret(s))", len(secrets))
	envContent := RenderEnv(eff, ref, secrets)
	if err := a.Runner.WriteFile(EnvFilePath(name), envContent, 0o640); err != nil {
		return fmt.Errorf("write env file: %w", err)
	}
	if res, err := a.Runner.Run(fmt.Sprintf("sudo chown root:%s %s && sudo chmod 0640 %s",
		eff.String("runtime.user", "svc-alerts"), EnvFilePath(name), EnvFilePath(name))); err != nil {
		return err
	} else if !res.OK() {
		return fmt.Errorf("secure env file: %s", strings.TrimSpace(res.Stderr))
	}

	a.logf("  - write unit %s", UnitName(name))
	if err := a.Runner.WriteFile(UnitPath(name), sp.Unit, 0o644); err != nil {
		return fmt.Errorf("write unit: %w", err)
	}

	// Flip `current` atomically: ln -sfn + mv means a restart can never see a
	// half-swapped tree.
	a.logf("  - activate release %s", ref)
	activate := fmt.Sprintf(
		"sudo -u %s ln -sfn %s %s.tmp && sudo -u %s mv -Tf %s.tmp %s",
		eff.String("runtime.user", "svc-alerts"), release, CurrentLink(name),
		eff.String("runtime.user", "svc-alerts"), CurrentLink(name), CurrentLink(name))
	if res, err := a.Runner.Run(activate); err != nil {
		return err
	} else if !res.OK() {
		return fmt.Errorf("activate release: %s", strings.TrimSpace(res.Stderr))
	}

	a.logf("  - reload systemd and restart")
	restart := fmt.Sprintf(
		"sudo systemctl daemon-reload && sudo systemctl enable %s && sudo systemctl restart %s",
		UnitName(name), UnitName(name))
	if res, err := a.Runner.Run(restart); err != nil {
		return err
	} else if !res.OK() {
		return fmt.Errorf("restart: %s", strings.TrimSpace(res.Stderr))
	}

	// Phase 3: the post-deploy gate. Everything above only proves the deploy
	// mechanics worked; this proves the service actually came up.
	if !a.Opts.SkipGates && !a.Opts.DryRun {
		if err := a.postDeployGate(eff); err != nil {
			return err
		}
	} else if a.Opts.SkipGates {
		a.logf("  ! post-deploy gate SKIPPED (--skip-gates)")
	}

	// Record what is now deployed, for the next plan to observe.
	manifest := Manifest{
		Service:    name,
		Ref:        ref,
		UnitHash:   sp.UnitHash,
		EnvHash:    sp.EnvHash,
		DeployedAt: time.Now().UTC().Format(time.RFC3339),
		DeployedBy: plan.CreatedBy,
		PlanID:     plan.ID,
	}
	raw, _ := json.MarshalIndent(manifest, "", "  ")
	if err := a.Runner.WriteFile(ManifestPath(name), string(raw)+"\n", 0o644); err != nil {
		return fmt.Errorf("write manifest: %w", err)
	}

	a.pruneReleases(name, eff)
	return nil
}

// postDeployGate verifies the service is genuinely healthy, not merely started.
func (a *Applier) postDeployGate(eff fleet.Effective) error {
	name := eff.Name
	grace := eff.Int("health.startup_grace_sec", 60)
	port := eff.Int("health.metrics.port", 0)
	heartbeat := eff.Int("health.heartbeat_interval_sec", 300)

	a.logf("  - gate: waiting %ds for startup", grace)
	time.Sleep(time.Duration(grace) * time.Second)

	res, err := a.Runner.Run(fmt.Sprintf("systemctl is-active %s", UnitName(name)))
	if err != nil {
		return fmt.Errorf("gate: query unit state: %w", err)
	}
	state := strings.TrimSpace(res.Stdout)
	if state != "active" {
		logs, _ := a.Runner.Run(fmt.Sprintf("journalctl -u %s -n 30 --no-pager", UnitName(name)))
		return fmt.Errorf("gate: unit is %q after %ds, expected active\n%s",
			state, grace, strings.TrimSpace(logs.Stdout))
	}

	if !eff.Bool("health.metrics.enabled", true) || port == 0 {
		a.logf("  - gate: unit active (no health endpoint declared)")
		return nil
	}

	// Poll /healthz until the heartbeat is fresh. The service reports 503
	// while its heartbeat is stale, so this distinguishes "process is up"
	// from "poll loop is actually running" — the failure mode that a plain
	// systemctl check misses entirely.
	deadline := time.Now().Add(time.Duration(max(heartbeat, 60)) * time.Second)
	probe := fmt.Sprintf("curl -fsS --max-time 5 http://127.0.0.1:%d/healthz", port)
	var lastBody string
	for time.Now().Before(deadline) {
		res, err := a.Runner.Run(probe)
		if err == nil && res.OK() {
			lastBody = res.Stdout
			var body struct {
				Status string `json:"status"`
			}
			if json.Unmarshal([]byte(res.Stdout), &body) == nil && body.Status == "ok" {
				a.logf("  - gate: health endpoint reports ok")
				return nil
			}
		} else if res.Stdout != "" {
			lastBody = res.Stdout
		}
		time.Sleep(5 * time.Second)
	}
	return fmt.Errorf("gate: %s never reported healthy on :%d within %ds\nlast response: %s",
		name, port, heartbeat, strings.TrimSpace(lastBody))
}

// rollbackTo re-applies a previous ref for one service.
func (a *Applier) rollbackTo(plan Plan, sp ServicePlan, ref string) error {
	if ref == "" {
		return fmt.Errorf("no previous ref to roll back to")
	}
	// Rollback is not a special code path — it is an apply of a different
	// desired ref. That is the whole payoff of pinning to immutable tags
	// (docs/spec.md, D2): the previous spec fully determines the previous
	// code, so "go back" and "go forward" are the same operation.
	prev := sp
	prev.DesiredRef = ref
	prev.CurrentRef = sp.DesiredRef
	prev.Action = "update"

	// The unit and env embed the ref, so re-render at the target ref.
	prev.Unit = RenderUnit(sp.Effective, a.Target)
	prev.UnitHash = Hash(prev.Unit)
	placeholders := map[string]string{}
	for _, n := range prev.SecretNames {
		placeholders[n] = SecretPlaceholder
	}
	prev.EnvHash = Hash(RenderEnv(sp.Effective, ref, placeholders))

	start := time.Now()
	err := a.applyService(plan, prev)
	_ = a.Audit.Append(audit.Entry{
		Event:    "rollback",
		Service:  sp.Service,
		PlanID:   plan.ID,
		Actor:    plan.CreatedBy,
		Target:   a.Runner.Describe(),
		FromRef:  sp.DesiredRef,
		ToRef:    ref,
		Outcome:  outcome(err),
		Duration: time.Since(start).Round(time.Millisecond).String(),
		Detail:   map[string]any{"automatic": true},
	})
	return err
}

// pruneReleases keeps a bounded number of previous releases on disk. Three
// is enough to roll back through a bad day and few enough not to fill the
// volume; older tags are always re-fetchable from the repo.
func (a *Applier) pruneReleases(name string, eff fleet.Effective) {
	keep := 3
	cmd := fmt.Sprintf(
		"ls -1dt %s/releases/*/ 2>/dev/null | tail -n +%d | xargs -r sudo rm -rf",
		ServiceRoot(name), keep+1)
	if res, err := a.Runner.Run(cmd); err != nil || !res.OK() {
		a.logf("  ~ note: release pruning skipped")
	}
}

// RunValidator shells out to tools/validate.py — the single validation entry
// point shared by local runs, CI, and this gate.
func RunValidator(root string) error {
	cmd := exec.Command("python3", "tools/validate.py")
	cmd.Dir = root
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("tools/validate.py failed:\n%s", strings.TrimSpace(string(out)))
	}
	return nil
}

// safeRef is the shape a source ref is allowed to have. The schema already
// constrains `spec.source.ref` to a semver tag, but this value reaches a
// subprocess and a `git clone --branch`, so it is re-checked at the point of
// use: a validator that runs earlier is a different guarantee from a check
// that runs here, and only one of them is still true if the call site moves.
var safeRef = regexp.MustCompile(`^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$`)

// checkRefExists confirms a tag is real before a deploy tries to fetch it.
// Catching a typo here costs a second; catching it after the restart costs
// an outage.
func checkRefExists(root, ref string) error {
	if !safeRef.MatchString(ref) {
		return fmt.Errorf("ref %q is not a semver tag; specs may only pin immutable tags "+
			"(docs/spec.md, D2)", ref)
	}
	// safeRef above constrains ref to a semver tag before it reaches here.
	cmd := exec.Command("git", "rev-parse", "--verify", "--quiet", ref+"^{tag}") //nolint:gosec
	cmd.Dir = root
	if err := cmd.Run(); err != nil {
		// A lightweight tag has no tag object; fall back to a commit lookup.
		cmd = exec.Command("git", "rev-parse", "--verify", "--quiet", ref) //nolint:gosec // see safeRef
		cmd.Dir = root
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("git tag %q does not exist in %s; "+
				"tag the release before deploying it", ref, root)
		}
	}
	return nil
}

func outcome(err error) string {
	if err != nil {
		return "failed"
	}
	return "success"
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

var _ = os.Getenv // retained: os is used by currentUser in plan.go
