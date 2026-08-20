// Package fleet loads and interprets the declarative fleet spec.
//
// Everything downstream — plan, apply, rollback, drift — works from the
// types here, so this package is the only place that knows the YAML shape.
// It does not validate: validation is tools/validate.py, and running it is
// the first gate of every plan (docs/spec.md, D6). Duplicating those rules
// in Go would create a second source of truth that silently drifts from the
// schema.
package fleet

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/goccy/go-yaml"
)

const APIVersion = "alertplatform/v1"

// Fleet is fleet/fleet.yaml.
type Fleet struct {
	APIVersion   string            `yaml:"apiVersion"`
	Kind         string            `yaml:"kind"`
	Metadata     FleetMetadata     `yaml:"metadata"`
	Targets      map[string]Target `yaml:"targets"`
	Defaults     map[string]any    `yaml:"defaults"`
	ChangePolicy ChangePolicy      `yaml:"change_policy"`
}

type FleetMetadata struct {
	Name  string `yaml:"name" json:"name"`
	Owner string `yaml:"owner" json:"owner"`
	Repo  string `yaml:"repo" json:"repo"`
}

type Target struct {
	Address  string `yaml:"address" json:"address"`
	User     string `yaml:"user" json:"user"`
	Platform string `yaml:"platform" json:"platform"`
}

type ChangePolicy struct {
	MaxConcurrentDeploys   int    `yaml:"max_concurrent_deploys" json:"max_concurrent_deploys"`
	RequirePlanBeforeApply bool   `yaml:"require_plan_before_apply" json:"require_plan_before_apply"`
	AuditLog               string `yaml:"audit_log" json:"audit_log"`
}

// Service is one fleet/services/*.yaml.
type Service struct {
	APIVersion string          `yaml:"apiVersion"`
	Kind       string          `yaml:"kind"`
	Metadata   ServiceMetadata `yaml:"metadata"`
	Spec       map[string]any  `yaml:"spec"`

	// Path is where this spec was read from. Carried for error messages:
	// "port 9101 is already taken" is only actionable with a filename.
	Path string `yaml:"-"`
}

type ServiceMetadata struct {
	Name        string `yaml:"name"`
	Description string `yaml:"description"`
	Tier        string `yaml:"tier"`
}

// Repo is a loaded fleet: one Fleet document plus every Service spec.
type Repo struct {
	Root     string
	Fleet    Fleet
	Services []Service
}

// Load reads fleet/fleet.yaml and fleet/services/*.yaml from a repo root.
func Load(root string) (*Repo, error) {
	fleetPath := filepath.Join(root, "fleet", "fleet.yaml")
	raw, err := os.ReadFile(fleetPath)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", fleetPath, err)
	}
	var f Fleet
	if err := yaml.Unmarshal(raw, &f); err != nil {
		return nil, fmt.Errorf("parse %s: %w", fleetPath, err)
	}
	if f.APIVersion != APIVersion || f.Kind != "Fleet" {
		return nil, fmt.Errorf("%s: expected apiVersion %s kind Fleet, got %s/%s",
			fleetPath, APIVersion, f.APIVersion, f.Kind)
	}

	matches, err := filepath.Glob(filepath.Join(root, "fleet", "services", "*.yaml"))
	if err != nil {
		return nil, err
	}
	sort.Strings(matches)

	repo := &Repo{Root: root, Fleet: f}
	for _, path := range matches {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("read %s: %w", path, err)
		}
		var s Service
		if err := yaml.Unmarshal(raw, &s); err != nil {
			return nil, fmt.Errorf("parse %s: %w", path, err)
		}
		s.Path = path
		repo.Services = append(repo.Services, s)
	}
	if len(repo.Services) == 0 {
		return nil, fmt.Errorf("no service specs found under %s/fleet/services", root)
	}
	return repo, nil
}

// Service returns one spec by metadata.name.
func (r *Repo) Service(name string) (Service, error) {
	for _, s := range r.Services {
		if s.Metadata.Name == name {
			return s, nil
		}
	}
	names := make([]string, 0, len(r.Services))
	for _, s := range r.Services {
		names = append(names, s.Metadata.Name)
	}
	return Service{}, fmt.Errorf("no service named %q; known services: %v", name, names)
}

// DefaultTarget resolves the host a service deploys to. Phase 1 models a
// single host; the lookup exists so multi-target support is a change here
// rather than everywhere.
func (r *Repo) DefaultTarget() (Target, error) {
	t, ok := r.Fleet.Targets["default_host"]
	if !ok {
		return Target{}, fmt.Errorf("fleet.targets.default_host is not defined")
	}
	return t, nil
}
