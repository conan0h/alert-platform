package engine

import (
	"strings"
	"testing"

	"github.com/conanohara/alert-platform/internal/fleet"
)

// The rest of this package's tests run against testdata/fleet, so that a
// release rolling `source.ref` cannot fail a test about unit rendering. That
// decoupling would be a regression if it also stopped anyone noticing that
// the real specs no longer render — so this file keeps the live fleet under
// test, asserting only properties that hold for every valid fleet.
//
// Nothing here may reference a version, a port, a service name or a count.
// If a change to fleet/ breaks this test, the fleet is genuinely broken;
// if it breaks because a release happened, the assertion does not belong here.
func TestLiveFleetSpecsRenderAndStayDeployable(t *testing.T) {
	repo, err := fleet.Load(repoRoot(t))
	if err != nil {
		t.Fatalf("the repo's own fleet/ must load: %v", err)
	}
	target, err := repo.DefaultTarget()
	if err != nil {
		t.Fatal(err)
	}

	ports := map[int]string{}
	for _, svc := range repo.Services {
		eff := fleet.Resolve(repo.Fleet, svc)
		name := eff.Name

		// Every pinned ref must survive the guard that stands in front of
		// `git clone --branch`. A branch or a typo here is undeployable, and
		// finding that out at apply time costs an outage.
		ref := eff.String("source.ref", "")
		if err := checkRefExists(t.TempDir(), ref); err != nil && !strings.Contains(err.Error(), "does not exist") {
			t.Errorf("%s: source.ref %q would be refused before it reached git: %v", name, ref, err)
		}

		unit := RenderUnit(eff, target)
		for _, want := range []string{"[Unit]", "[Service]", "[Install]", "ExecStart=", "NoNewPrivileges=true"} {
			if !strings.Contains(unit, want) {
				t.Errorf("%s: rendered unit is missing %q", name, want)
			}
		}

		placeholders := map[string]string{}
		for _, secret := range eff.SecretNames() {
			placeholders[secret] = SecretPlaceholder
		}
		env := RenderEnv(eff, ref, placeholders)
		if !strings.Contains(env, "ALERT_SERVICE_NAME="+name) {
			t.Errorf("%s: rendered env does not identify the service", name)
		}
		if !strings.Contains(env, "ALERT_DEPLOYED_REF="+ref) {
			t.Errorf("%s: rendered env does not record the deployed ref", name)
		}

		// tools/validate.py enforces this too, but it is the invariant that
		// keeps two services from fighting over a scrape target, so the
		// engine asserts it against the specs it would actually deploy.
		port := eff.Int("health.metrics.port", 0)
		if other, taken := ports[port]; taken {
			t.Errorf("metrics port %d claimed by both %s and %s", port, other, name)
		}
		ports[port] = name
	}
}
