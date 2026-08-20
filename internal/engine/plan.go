package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

// SecretPlaceholder stands in for a resolved secret when rendering the
// environment for planning and hashing. Plans are printed to terminals,
// pasted into tickets and stored on disk; they must never contain a
// credential. Apply substitutes real values at deploy time.
const SecretPlaceholder = "<resolved-at-apply>" //nolint:gosec // not a credential: this is the string that replaces one

// Change is one difference between desired and observed state.
type Change struct {
	Field    string `json:"field"`
	Observed string `json:"observed"`
	Desired  string `json:"desired"`
	Reason   string `json:"reason,omitempty"`
}

// ServicePlan is the change set for one service.
type ServicePlan struct {
	Service     string          `json:"service"`
	Action      string          `json:"action"` // create | update | noop
	CurrentRef  string          `json:"current_ref"`
	DesiredRef  string          `json:"desired_ref"`
	Changes     []Change        `json:"changes"`
	UnitHash    string          `json:"unit_hash"`
	EnvHash     string          `json:"env_hash"`
	SecretNames []string        `json:"secret_names"`
	Unit        string          `json:"-"`
	Effective   fleet.Effective `json:"-"`
}

func (p ServicePlan) IsNoop() bool { return p.Action == "noop" }

// Plan is a full change set, one entry per service considered.
type Plan struct {
	ID        string        `json:"id"`
	CreatedAt time.Time     `json:"created_at"`
	CreatedBy string        `json:"created_by"`
	RepoRoot  string        `json:"repo_root"`
	Target    string        `json:"target"`
	Services  []ServicePlan `json:"services"`
}

func (p Plan) Pending() []ServicePlan {
	var out []ServicePlan
	for _, sp := range p.Services {
		if !sp.IsNoop() {
			out = append(out, sp)
		}
	}
	return out
}

// Observed is what the host reports about a service right now.
type Observed struct {
	Exists     bool
	UnitExists bool
	Active     string // active | inactive | failed | unknown
	Enabled    string
	Manifest   Manifest
	UnitHash   string
}

// Observe reads current state from the target. Read-only by construction:
// nothing here mutates the host, so `plan` is always safe to run.
func Observe(r exec.Runner, service string) (Observed, error) {
	obs := Observed{Active: "unknown", Enabled: "unknown"}

	res, err := r.Run(fmt.Sprintf("cat %s 2>/dev/null || true", ManifestPath(service)))
	if err != nil {
		return obs, fmt.Errorf("read manifest: %w", err)
	}
	if strings.TrimSpace(res.Stdout) != "" {
		if err := json.Unmarshal([]byte(res.Stdout), &obs.Manifest); err == nil {
			obs.Exists = true
		}
	}

	res, err = r.Run(fmt.Sprintf("sha256sum %s 2>/dev/null | cut -c1-16 || true", UnitPath(service)))
	if err != nil {
		return obs, fmt.Errorf("hash unit: %w", err)
	}
	if h := strings.TrimSpace(res.Stdout); h != "" {
		obs.UnitExists = true
		obs.UnitHash = h
	}

	res, err = r.Run(fmt.Sprintf(
		"systemctl show %s --property=ActiveState,UnitFileState --value 2>/dev/null || true",
		UnitName(service)))
	if err != nil {
		return obs, fmt.Errorf("query systemd: %w", err)
	}
	lines := strings.Fields(strings.TrimSpace(res.Stdout))
	if len(lines) > 0 {
		obs.Active = lines[0]
	}
	if len(lines) > 1 {
		obs.Enabled = lines[1]
	}
	return obs, nil
}

// BuildPlan computes the change set for the named services (all if empty).
func BuildPlan(repo *fleet.Repo, r exec.Runner, only []string) (Plan, error) {
	target, err := repo.DefaultTarget()
	if err != nil {
		return Plan{}, err
	}

	wanted := map[string]bool{}
	for _, name := range only {
		wanted[name] = true
	}

	plan := Plan{
		CreatedAt: time.Now().UTC(),
		CreatedBy: currentUser(),
		RepoRoot:  repo.Root,
		Target:    r.Describe(),
	}

	for _, svc := range repo.Services {
		if len(wanted) > 0 && !wanted[svc.Metadata.Name] {
			continue
		}
		eff := fleet.Resolve(repo.Fleet, svc)
		desiredRef := eff.String("source.ref", "")

		obs, err := Observe(r, svc.Metadata.Name)
		if err != nil {
			return Plan{}, fmt.Errorf("observe %s: %w", svc.Metadata.Name, err)
		}

		unit := RenderUnit(eff, target)
		unitHash := Hash(unit)

		secretNames := eff.SecretNames()
		placeholders := map[string]string{}
		for _, n := range secretNames {
			placeholders[n] = SecretPlaceholder
		}
		envHash := Hash(RenderEnv(eff, desiredRef, placeholders))

		sp := ServicePlan{
			Service:     svc.Metadata.Name,
			CurrentRef:  obs.Manifest.Ref,
			DesiredRef:  desiredRef,
			UnitHash:    unitHash,
			EnvHash:     envHash,
			SecretNames: secretNames,
			Unit:        unit,
			Effective:   eff,
		}

		switch {
		case !obs.Exists || !obs.UnitExists:
			sp.Action = "create"
			sp.Changes = append(sp.Changes, Change{
				Field: "service", Observed: "absent", Desired: "installed",
				Reason: "no deploy manifest or unit file on the target",
			})
		default:
			if obs.Manifest.Ref != desiredRef {
				sp.Changes = append(sp.Changes, Change{
					Field: "source.ref", Observed: obs.Manifest.Ref, Desired: desiredRef,
					Reason: "code change",
				})
			}
			if obs.UnitHash != unitHash {
				sp.Changes = append(sp.Changes, Change{
					Field: "systemd unit", Observed: obs.UnitHash, Desired: unitHash,
					Reason: "runtime or resource settings changed, or the unit was edited on the host",
				})
			}
			if obs.Manifest.EnvHash != envHash {
				sp.Changes = append(sp.Changes, Change{
					Field: "environment", Observed: shortHash(obs.Manifest.EnvHash), Desired: envHash,
					Reason: "polling, delivery, health or state config changed",
				})
			}
			if obs.Active != "active" {
				sp.Changes = append(sp.Changes, Change{
					Field: "state", Observed: obs.Active, Desired: "active",
					Reason: "service is not running",
				})
			}
			if obs.Enabled != "enabled" {
				sp.Changes = append(sp.Changes, Change{
					Field: "enabled", Observed: obs.Enabled, Desired: "enabled",
					Reason: "service would not start on reboot",
				})
			}
			if len(sp.Changes) == 0 {
				sp.Action = "noop"
			} else {
				sp.Action = "update"
			}
		}
		plan.Services = append(plan.Services, sp)
	}

	plan.ID = plan.fingerprint()
	return plan, nil
}

// fingerprint identifies a plan by its content. `apply` recomputes it from
// the current specs and refuses to run if it has changed — which is what
// stops "plan reviewed on Monday, applied on Friday against edited specs".
func (p Plan) fingerprint() string {
	h := sha256.New()
	for _, sp := range p.Services {
		fmt.Fprintf(h, "%s|%s|%s|%s|%s\n",
			sp.Service, sp.Action, sp.DesiredRef, sp.UnitHash, sp.EnvHash)
	}
	return hex.EncodeToString(h.Sum(nil))[:12]
}

// Save writes the plan to disk for later apply.
func (p Plan) Save(path string) error {
	raw, err := json.MarshalIndent(p, "", "  ")
	if err != nil {
		return err
	}
	// 0644: plans are review artefacts and contain no secret values by
	// construction — see TestPlanNeverContainsSecretValues.
	return os.WriteFile(path, append(raw, '\n'), 0o644) //nolint:gosec
}

func LoadPlan(path string) (Plan, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Plan{}, err
	}
	var p Plan
	if err := json.Unmarshal(raw, &p); err != nil {
		return Plan{}, fmt.Errorf("parse plan %s: %w", path, err)
	}
	return p, nil
}

// Render prints a plan the way an operator reads it before approving.
func (p Plan) Render(w *strings.Builder) {
	fmt.Fprintf(w, "Plan %s   target: %s   %s\n\n", p.ID, p.Target,
		p.CreatedAt.Format(time.RFC3339))

	pending := p.Pending()
	if len(pending) == 0 {
		fmt.Fprintf(w, "No changes. %d service(s) match desired state.\n", len(p.Services))
		return
	}

	for _, sp := range p.Services {
		if sp.IsNoop() {
			fmt.Fprintf(w, "  = %-16s no changes (%s)\n", sp.Service, sp.CurrentRef)
			continue
		}
		marker := "~"
		if sp.Action == "create" {
			marker = "+"
		}
		fmt.Fprintf(w, "  %s %-16s %s\n", marker, sp.Service, strings.ToUpper(sp.Action))
		for _, c := range sp.Changes {
			fmt.Fprintf(w, "      %-14s %s -> %s\n", c.Field, orNone(c.Observed), c.Desired)
			if c.Reason != "" {
				fmt.Fprintf(w, "      %-14s (%s)\n", "", c.Reason)
			}
		}
		if len(sp.SecretNames) > 0 {
			fmt.Fprintf(w, "      %-14s %s\n", "secrets", strings.Join(sp.SecretNames, ", "))
		}
		fmt.Fprintln(w)
	}

	fmt.Fprintf(w, "%d to change, %d unchanged.\n",
		len(pending), len(p.Services)-len(pending))
}

func orNone(s string) string {
	if s == "" {
		return "(none)"
	}
	return s
}

func shortHash(s string) string {
	if len(s) > 16 {
		return s[:16]
	}
	return orNone(s)
}

func currentUser() string {
	for _, key := range []string{"SUDO_USER", "USER", "LOGNAME"} {
		if v := os.Getenv(key); v != "" {
			return v
		}
	}
	return "unknown"
}

// SortServices keeps plan output and apply order stable. Critical-tier
// services deploy last: if a shared change is going to break something, it
// should break the standard-tier service first, while the gate can still
// stop the rollout.
func SortServices(plans []ServicePlan) {
	sort.SliceStable(plans, func(i, j int) bool {
		ti := plans[i].Effective.Tier == "critical"
		tj := plans[j].Effective.Tier == "critical"
		if ti != tj {
			return !ti
		}
		return plans[i].Service < plans[j].Service
	})
}
