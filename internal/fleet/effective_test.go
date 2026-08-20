package fleet

import (
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

// repoRoot walks up from this file so tests run from any working directory.
func repoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate test file")
	}
	return filepath.Join(filepath.Dir(file), "..", "..")
}

func TestLoadReadsFleetAndAllServices(t *testing.T) {
	repo, err := Load(repoRoot(t))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if repo.Fleet.Metadata.Name != "market-alert-suite" {
		t.Errorf("fleet name = %q", repo.Fleet.Metadata.Name)
	}
	if len(repo.Services) != 4 {
		t.Fatalf("expected 4 services, got %d", len(repo.Services))
	}
	if _, err := repo.Service("edgar-mna"); err != nil {
		t.Errorf("edgar-mna missing: %v", err)
	}
	if _, err := repo.Service("nope"); err == nil {
		t.Error("expected an error naming the known services")
	}
}

func TestScalarOverridesDefault(t *testing.T) {
	f := Fleet{Defaults: map[string]any{
		"runtime": map[string]any{"memory_max": "256M", "user": "svc-alerts"},
	}}
	s := Service{
		Metadata: ServiceMetadata{Name: "x"},
		Spec:     map[string]any{"resources": map[string]any{"memory_max": "384M"}},
	}
	e := Resolve(f, s)
	if got := e.String("runtime.memory_max", ""); got != "256M" {
		t.Errorf("fleet default clobbered: %q", got)
	}
	if got := e.String("resources.memory_max", ""); got != "384M" {
		t.Errorf("service override lost: %q", got)
	}
	if got := e.String("runtime.user", ""); got != "svc-alerts" {
		t.Errorf("unrelated default lost: %q", got)
	}
}

func TestMapsDeepMergeKeyByKey(t *testing.T) {
	f := Fleet{Defaults: map[string]any{
		"health": map[string]any{
			"heartbeat_interval_sec": 300,
			"startup_grace_sec":      60,
			"metrics":                map[string]any{"enabled": true},
		},
	}}
	s := Service{Spec: map[string]any{
		"health": map[string]any{"metrics": map[string]any{"port": 9104}},
	}}
	e := Resolve(f, s)

	if got := e.Int("health.heartbeat_interval_sec", 0); got != 300 {
		t.Errorf("sibling key lost in merge: %d", got)
	}
	if got := e.Int("health.metrics.port", 0); got != 9104 {
		t.Errorf("nested override lost: %d", got)
	}
	if !e.Bool("health.metrics.enabled", false) {
		t.Error("nested default lost when a sibling key was added")
	}
}

// The list rule is the one people get wrong, and getting it wrong silently
// changes what a service polls — so it gets its own test.
func TestListsAreReplacedNotConcatenated(t *testing.T) {
	f := Fleet{Defaults: map[string]any{
		"polling": map[string]any{"forms": []any{"8-K", "10-Q"}},
	}}
	s := Service{Spec: map[string]any{
		"polling": map[string]any{"forms": []any{"SC 13D"}},
	}}
	e := Resolve(f, s)

	got, _ := e.Get("polling.forms")
	want := []any{"SC 13D"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("lists must be replaced wholesale: got %v, want %v", got, want)
	}
}

func TestServiceOverrideDoesNotMutateFleetDefaults(t *testing.T) {
	defaults := map[string]any{"runtime": map[string]any{"memory_max": "256M"}}
	f := Fleet{Defaults: defaults}

	Resolve(f, Service{Spec: map[string]any{
		"runtime": map[string]any{"memory_max": "1G"},
	}})
	// A second service must still see the original default: shared mutable
	// state here would make the effective config depend on file ordering.
	e := Resolve(f, Service{Spec: map[string]any{}})
	if got := e.String("runtime.memory_max", ""); got != "256M" {
		t.Errorf("fleet defaults were mutated by a previous resolve: %q", got)
	}
}

func TestSecretNamesAreDiscoveredAnywhereInTheSpec(t *testing.T) {
	repo, err := Load(repoRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	svc, _ := repo.Service("edgar-mna")
	e := Resolve(repo.Fleet, svc)

	got := e.SecretNames()
	want := []string{"edgar_user_agent", "tg_bot_token", "tg_chat_mna"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("secret discovery = %v, want %v", got, want)
	}
}

func TestIntCoercesYAMLNumberShapes(t *testing.T) {
	e := Effective{Config: map[string]any{
		"a": map[string]any{"i": 42, "f": float64(43), "s": "44"},
	}}
	for path, want := range map[string]int{"a.i": 42, "a.f": 43, "a.s": 44} {
		if got := e.Int(path, -1); got != want {
			t.Errorf("Int(%q) = %d, want %d", path, got, want)
		}
	}
	if got := e.Int("a.missing", 7); got != 7 {
		t.Errorf("fallback ignored: %d", got)
	}
}
