// Command alertctl is the control plane for the market alert suite.
//
// It reads the declarative fleet spec, computes the difference between
// desired and observed state, and reconciles it under the change policy the
// fleet declares. Every mutation is planned first, gated before and after,
// and recorded in an append-only audit log.
//
//	alertctl validate                     schema + fleet invariants
//	alertctl plan [-service NAME]         show what would change
//	alertctl apply -plan FILE             reconcile, with gates and rollback
//	alertctl status                       what is deployed right now
//	alertctl drift                        exit 1 if the host has drifted
//	alertctl rollback -service NAME       return to the last good ref
//	alertctl render -service NAME         print the unit and env that would be written
//	alertctl history [-service NAME]      read the audit log
//	alertctl serve [-addr HOST:PORT]      read-only operator console
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/conanohara/alert-platform/internal/audit"
	"github.com/conanohara/alert-platform/internal/engine"
	pexec "github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

const usage = `alertctl — control plane for the market alert suite

Usage:
  alertctl <command> [flags]

Commands:
  validate    Validate every spec against the schema and fleet invariants
  plan        Compute the change set between desired and observed state
  apply       Execute a plan (gates, sequential deploy, audit, auto-rollback)
  status      Show what is deployed on the target
  drift       Exit non-zero if the target has drifted from desired state
  rollback    Roll a service back to its last successful ref
  render      Print the systemd unit and environment for a service
  history     Print audit log entries
  serve       Serve the read-only operator console (loopback by default)

Common flags:
  -root PATH      Repo root (default ".")
  -target MODE    ssh | local | dry (default "ssh")
  -service NAME   Limit to one service

Run 'alertctl <command> -h' for command flags.
`

func main() {
	if len(os.Args) < 2 {
		fmt.Fprint(os.Stderr, usage)
		os.Exit(2)
	}

	cmd := os.Args[1]
	args := os.Args[2:]

	var err error
	switch cmd {
	case "validate":
		err = cmdValidate(args)
	case "plan":
		err = cmdPlan(args)
	case "apply":
		err = cmdApply(args)
	case "status":
		err = cmdStatus(args)
	case "drift":
		err = cmdDrift(args)
	case "rollback":
		err = cmdRollback(args)
	case "render":
		err = cmdRender(args)
	case "history":
		err = cmdHistory(args)
	case "serve":
		err = cmdServe(args)
	case "-h", "--help", "help":
		fmt.Print(usage)
		return
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n\n%s", cmd, usage)
		os.Exit(2)
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "\nerror: %v\n", err)
		os.Exit(1)
	}
}

// -- shared setup ----------------------------------------------------------

type common struct {
	root    string
	target  string
	service string
	sshRoot string
}

func (c *common) bind(fs *flag.FlagSet) {
	fs.StringVar(&c.root, "root", ".", "repo root")
	fs.StringVar(&c.target, "target", "ssh", "ssh | local | dry")
	fs.StringVar(&c.service, "service", "", "limit to one service")
	fs.StringVar(&c.sshRoot, "local-root", "", "filesystem prefix for -target local (testing)")
}

func (c *common) load() (*fleet.Repo, pexec.Runner, fleet.Target, error) {
	repo, err := fleet.Load(c.root)
	if err != nil {
		return nil, nil, fleet.Target{}, err
	}
	target, err := repo.DefaultTarget()
	if err != nil {
		return nil, nil, fleet.Target{}, err
	}

	var runner pexec.Runner
	switch c.target {
	case "ssh":
		runner = pexec.NewSSH(target.Address, target.User)
	case "local":
		runner = pexec.NewLocal(c.sshRoot)
	case "dry":
		runner = pexec.NewDry(pexec.NewLocal(c.sshRoot))
	default:
		return nil, nil, fleet.Target{}, fmt.Errorf("unknown -target %q (want ssh, local or dry)", c.target)
	}
	return repo, runner, target, nil
}

func (c *common) services() []string {
	if c.service == "" {
		return nil
	}
	return []string{c.service}
}

// -- commands --------------------------------------------------------------

func cmdValidate(args []string) error {
	fs := flag.NewFlagSet("validate", flag.ExitOnError)
	root := fs.String("root", ".", "repo root")
	_ = fs.Parse(args)

	if err := engine.RunValidator(*root); err != nil {
		return err
	}
	repo, err := fleet.Load(*root)
	if err != nil {
		return err
	}
	fmt.Printf("OK: %d service spec(s) valid, fleet invariants hold.\n", len(repo.Services))
	return nil
}

func cmdPlan(args []string) error {
	fs := flag.NewFlagSet("plan", flag.ExitOnError)
	var c common
	c.bind(fs)
	out := fs.String("out", "", "write the plan to this file for `alertctl apply -plan`")
	skipValidate := fs.Bool("skip-validate", false, "skip the validation gate (not recommended)")
	_ = fs.Parse(args)

	repo, runner, _, err := c.load()
	if err != nil {
		return err
	}

	if !*skipValidate {
		if err := engine.RunValidator(repo.Root); err != nil {
			return fmt.Errorf("validation gate failed — fix the spec before planning:\n%w", err)
		}
	}
	if c.service != "" {
		if _, err := repo.Service(c.service); err != nil {
			return err
		}
	}

	plan, err := engine.BuildPlan(repo, runner, c.services())
	if err != nil {
		return err
	}

	var b strings.Builder
	plan.Render(&b)
	fmt.Print(b.String())

	if *out != "" {
		if err := plan.Save(*out); err != nil {
			return err
		}
		fmt.Printf("\nPlan saved to %s\nApply with: alertctl apply -plan %s\n", *out, *out)
	} else if len(plan.Pending()) > 0 {
		fmt.Printf("\nSave this plan to apply it:  alertctl plan -out plan.json\n")
	}
	return nil
}

func cmdApply(args []string) error {
	fs := flag.NewFlagSet("apply", flag.ExitOnError)
	var c common
	c.bind(fs)
	planPath := fs.String("plan", "", "plan file from `alertctl plan -out`")
	dryRun := fs.Bool("dry-run", false, "print every command without executing it")
	autoApprove := fs.Bool("auto-approve", false, "do not prompt for confirmation")
	skipGates := fs.Bool("skip-gates", false, "BREAK GLASS: skip post-deploy verification")
	noRollback := fs.Bool("no-rollback", false, "leave a failed deploy in place for debugging")
	_ = fs.Parse(args)

	repo, runner, target, err := c.load()
	if err != nil {
		return err
	}

	var plan engine.Plan
	if *planPath != "" {
		plan, err = engine.LoadPlan(*planPath)
		if err != nil {
			return err
		}
		// A saved plan carries no live Effective/Unit (they are derived, not
		// data), so rebuild them from the specs the fingerprint check pins.
		rebuilt, err := engine.BuildPlan(repo, runner, c.services())
		if err != nil {
			return err
		}
		if rebuilt.ID != plan.ID {
			return fmt.Errorf(
				"plan %s no longer matches the current specs and host state (now %s).\n"+
					"Re-run `alertctl plan -out %s` and review the change set again",
				plan.ID, rebuilt.ID, *planPath)
		}
		plan = rebuilt
	} else {
		if repo.Fleet.ChangePolicy.RequirePlanBeforeApply && !*dryRun {
			return fmt.Errorf(
				"change_policy.require_plan_before_apply is true: run\n" +
					"  alertctl plan -out plan.json\n" +
					"review the output, then\n" +
					"  alertctl apply -plan plan.json")
		}
		plan, err = engine.BuildPlan(repo, runner, c.services())
		if err != nil {
			return err
		}
	}

	pending := plan.Pending()
	if len(pending) == 0 {
		fmt.Println("No changes. Every service matches desired state.")
		return nil
	}

	var b strings.Builder
	plan.Render(&b)
	fmt.Print(b.String())

	if !*autoApprove && !*dryRun {
		fmt.Printf("\nApply these %d change(s) to %s? Type 'yes' to continue: ", len(pending), runner.Describe())
		var answer string
		_, _ = fmt.Scanln(&answer)
		if strings.TrimSpace(strings.ToLower(answer)) != "yes" {
			return fmt.Errorf("aborted by operator")
		}
	}

	resolver, err := engine.ResolverFor(repo.Fleet, runner, *dryRun)
	if err != nil {
		return err
	}
	log, err := audit.Open(auditPath(repo))
	if err != nil {
		return err
	}

	applier := &engine.Applier{
		Repo:     repo,
		Runner:   runner,
		Resolver: resolver,
		Audit:    log,
		Target:   target,
		Opts: engine.Options{
			DryRun:      *dryRun,
			AutoApprove: *autoApprove,
			SkipGates:   *skipGates,
			NoRollback:  *noRollback,
			Out:         os.Stdout,
		},
	}
	return applier.Apply(plan)
}

func cmdStatus(args []string) error {
	fs := flag.NewFlagSet("status", flag.ExitOnError)
	var c common
	c.bind(fs)
	asJSON := fs.Bool("json", false, "emit JSON")
	_ = fs.Parse(args)

	repo, runner, _, err := c.load()
	if err != nil {
		return err
	}

	type row struct {
		Service string `json:"service"`
		Ref     string `json:"ref"`
		State   string `json:"state"`
		Enabled string `json:"enabled"`
		Since   string `json:"deployed_at"`
		By      string `json:"deployed_by"`
	}
	var rows []row

	for _, svc := range repo.Services {
		if c.service != "" && svc.Metadata.Name != c.service {
			continue
		}
		obs, err := engine.Observe(runner, svc.Metadata.Name)
		if err != nil {
			return err
		}
		r := row{Service: svc.Metadata.Name, State: obs.Active, Enabled: obs.Enabled}
		if obs.Exists {
			r.Ref = obs.Manifest.Ref
			r.Since = obs.Manifest.DeployedAt
			r.By = obs.Manifest.DeployedBy
		} else {
			r.Ref = "(not deployed)"
		}
		rows = append(rows, r)
	}

	if *asJSON {
		raw, _ := json.MarshalIndent(rows, "", "  ")
		fmt.Println(string(raw))
		return nil
	}

	fmt.Printf("%-16s %-10s %-10s %-10s %-22s %s\n",
		"SERVICE", "REF", "STATE", "ENABLED", "DEPLOYED", "BY")
	for _, r := range rows {
		fmt.Printf("%-16s %-10s %-10s %-10s %-22s %s\n",
			r.Service, r.Ref, r.State, r.Enabled, orDash(r.Since), orDash(r.By))
	}
	return nil
}

func cmdDrift(args []string) error {
	fs := flag.NewFlagSet("drift", flag.ExitOnError)
	var c common
	c.bind(fs)
	_ = fs.Parse(args)

	repo, runner, _, err := c.load()
	if err != nil {
		return err
	}
	plan, err := engine.BuildPlan(repo, runner, c.services())
	if err != nil {
		return err
	}

	pending := plan.Pending()
	if len(pending) == 0 {
		fmt.Println("No drift: the target matches desired state.")
		return nil
	}

	var b strings.Builder
	plan.Render(&b)
	fmt.Print(b.String())
	// Non-zero exit so a scheduled run can page. Drift is not an error in
	// the CLI sense — it is a finding — but exit codes are the only thing a
	// cron job or CI check reliably reads.
	os.Exit(1)
	return nil
}

func cmdRollback(args []string) error {
	fs := flag.NewFlagSet("rollback", flag.ExitOnError)
	var c common
	c.bind(fs)
	to := fs.String("to", "", "ref to roll back to (default: last successful in the audit log)")
	autoApprove := fs.Bool("auto-approve", false, "do not prompt")
	_ = fs.Parse(args)

	if c.service == "" {
		return fmt.Errorf("-service is required for rollback")
	}
	repo, runner, _, err := c.load()
	if err != nil {
		return err
	}
	svc, err := repo.Service(c.service)
	if err != nil {
		return err
	}

	obs, err := engine.Observe(runner, c.service)
	if err != nil {
		return err
	}
	current := obs.Manifest.Ref

	targetRef := *to
	if targetRef == "" {
		log, err := audit.Open(auditPath(repo))
		if err != nil {
			return err
		}
		targetRef, err = log.LastSuccessfulRef(c.service, current)
		if err != nil {
			return fmt.Errorf("%w\nPass -to vX.Y.Z to name the ref explicitly", err)
		}
	}

	fmt.Printf("Rollback %s: %s -> %s\n", c.service, orNoneStr(current), targetRef)
	fmt.Println("\nRollback works by editing desired state, so the repo stays the source of truth:")
	fmt.Printf("\n  1. Set spec.source.ref to %s in %s\n", targetRef, svc.Path)
	fmt.Printf("  2. alertctl plan -service %s -out rollback.json\n", c.service)
	fmt.Printf("  3. alertctl apply -plan rollback.json\n")
	fmt.Println("\nThis is deliberate: a rollback that bypassed the spec would leave the")
	fmt.Println("repo claiming a version that is not running, and the next apply would")
	fmt.Println("silently roll forward again.")

	if !*autoApprove {
		fmt.Printf("\nRewrite %s now? Type 'yes' to continue: ", svc.Path)
		var answer string
		_, _ = fmt.Scanln(&answer)
		if strings.TrimSpace(strings.ToLower(answer)) != "yes" {
			return fmt.Errorf("aborted; no files changed")
		}
	}
	if err := rewriteRef(svc.Path, targetRef); err != nil {
		return err
	}
	fmt.Printf("\nUpdated %s to ref %s. Now run:\n  alertctl plan -service %s -out rollback.json\n",
		svc.Path, targetRef, c.service)
	return nil
}

func cmdRender(args []string) error {
	fs := flag.NewFlagSet("render", flag.ExitOnError)
	var c common
	c.bind(fs)
	_ = fs.Parse(args)

	if c.service == "" {
		return fmt.Errorf("-service is required for render")
	}
	repo, _, target, err := c.load()
	if err != nil {
		return err
	}
	svc, err := repo.Service(c.service)
	if err != nil {
		return err
	}
	eff := fleet.Resolve(repo.Fleet, svc)
	ref := eff.String("source.ref", "")

	placeholders := map[string]string{}
	for _, n := range eff.SecretNames() {
		placeholders[n] = engine.SecretPlaceholder
	}

	fmt.Printf("# %s\n%s\n", engine.UnitPath(c.service), engine.RenderUnit(eff, target))
	fmt.Printf("# %s\n%s", engine.EnvFilePath(c.service), engine.RenderEnv(eff, ref, placeholders))
	return nil
}

func cmdHistory(args []string) error {
	fs := flag.NewFlagSet("history", flag.ExitOnError)
	var c common
	c.bind(fs)
	limit := fs.Int("n", 20, "number of entries to show")
	_ = fs.Parse(args)

	repo, err := fleet.Load(c.root)
	if err != nil {
		return err
	}
	log, err := audit.Open(auditPath(repo))
	if err != nil {
		return err
	}
	entries, err := log.History(c.service)
	if err != nil {
		return err
	}
	if len(entries) == 0 {
		fmt.Printf("No audit entries in %s yet.\n", log.Path)
		return nil
	}
	if len(entries) > *limit {
		entries = entries[len(entries)-*limit:]
	}
	for _, e := range entries {
		fmt.Printf("%s  %-9s %-16s %-8s %s -> %s  (%s, %s)\n",
			e.Timestamp.Format(time.RFC3339), e.Event, e.Service, e.Outcome,
			orNoneStr(e.FromRef), orNoneStr(e.ToRef), e.Actor, e.Duration)
	}
	return nil
}

// -- helpers ---------------------------------------------------------------

// auditPath keeps the audit log next to the repo when running locally and at
// the fleet-declared path in production. Writing to /var/log from a laptop
// would just fail, and a failed audit write fails the deploy.
func auditPath(repo *fleet.Repo) string {
	declared := repo.Fleet.ChangePolicy.AuditLog
	if declared == "" {
		declared = "/var/log/alert-platform/audit.jsonl"
	}
	if f, err := os.OpenFile(declared, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640); err == nil {
		f.Close()
		return declared
	}
	return repo.Root + "/.alertctl/audit.jsonl"
}

func rewriteRef(specPath, ref string) error {
	raw, err := os.ReadFile(specPath)
	if err != nil {
		return err
	}
	lines := strings.Split(string(raw), "\n")
	changed := false
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "ref:") {
			indent := line[:len(line)-len(strings.TrimLeft(line, " "))]
			comment := ""
			if idx := strings.Index(line, "#"); idx >= 0 {
				comment = "  " + line[idx:]
			}
			lines[i] = fmt.Sprintf("%sref: %s%s", indent, ref, comment)
			changed = true
			break
		}
	}
	if !changed {
		return fmt.Errorf("no `ref:` line found in %s", specPath)
	}
	// 0644: this is a spec file in the repo, tracked in git and read by CI.
	return os.WriteFile(specPath, []byte(strings.Join(lines, "\n")), 0o644) //nolint:gosec
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func orNoneStr(s string) string {
	if s == "" {
		return "(none)"
	}
	return s
}
