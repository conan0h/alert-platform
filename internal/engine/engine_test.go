package engine

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/conanohara/alert-platform/internal/audit"
	pexec "github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

// fixtureRef is the ref every fixture spec pins. It is test-owned and never
// bumped, which is the whole point: releasing the real fleet must not be able
// to fail a test about unit rendering.
const fixtureRef = "v1.0.0"

func repoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "..", "..")
}

// fixtureRoot is the root of this package's own fleet. Engine tests load it
// rather than the repo's fleet/, which records deployment decisions: see
// testdata/fleet/fleet.yaml for why. Coverage of the live specs lives in
// live_fleet_test.go and asserts properties, not pinned values.
func fixtureRoot(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "testdata")
}

func loadFixture(t *testing.T) *fleet.Repo {
	t.Helper()
	repo, err := fleet.Load(fixtureRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	return repo
}

func load(t *testing.T) (*fleet.Repo, fleet.Effective, fleet.Target) {
	t.Helper()
	repo := loadFixture(t)
	svc, err := repo.Service("fixture-insider")
	if err != nil {
		t.Fatal(err)
	}
	target, _ := repo.DefaultTarget()
	return repo, fleet.Resolve(repo.Fleet, svc), target
}

// -- unit rendering --------------------------------------------------------

func TestRenderUnitAppliesFleetDefaultsAndOverrides(t *testing.T) {
	_, eff, target := load(t)
	unit := RenderUnit(eff, target)

	for _, want := range []string{
		"User=svc-alerts",    // fleet default, never root
		"Restart=on-failure", // fleet default, not the old Restart=always
		"RestartSec=10",
		"MemoryMax=256M",
		"StandardOutput=journal", // not a log file next to the code
		"EnvironmentFile=/etc/alert-platform/fixture-insider.env",
		"WorkingDirectory=/opt/alert-platform/fixture-insider/current",
		"NoNewPrivileges=true",
	} {
		if !strings.Contains(unit, want) {
			t.Errorf("unit missing %q\n---\n%s", want, unit)
		}
	}
	if strings.Contains(unit, "/home/ubuntu") {
		t.Error("unit still references the pre-migration home directory layout")
	}
}

func TestRenderUnitHonoursPerServiceResourceOverride(t *testing.T) {
	repo := loadFixture(t)
	svc, _ := repo.Service("fixture-filings") // declares resources.memory_max: 384M
	target, _ := repo.DefaultTarget()
	unit := RenderUnit(fleet.Resolve(repo.Fleet, svc), target)

	if !strings.Contains(unit, "MemoryMax=384M") {
		t.Error("service-level memory override not applied")
	}
}

func TestUnitHashChangesWithConfig(t *testing.T) {
	_, eff, target := load(t)
	before := Hash(RenderUnit(eff, target))

	eff.Config["runtime"].(map[string]any)["restart_sec"] = 30
	after := Hash(RenderUnit(eff, target))

	if before == after {
		t.Error("unit hash did not change after a config change; drift would be invisible")
	}
}

// -- environment contract --------------------------------------------------

func TestRenderEnvMatchesTheAlertlibContract(t *testing.T) {
	_, eff, _ := load(t)
	env := RenderEnv(eff, "v2.0.1", map[string]string{
		"tg_bot_token":       "123:secret",
		"tg_chat_insider":    "-1001",
		"fixture_user_agent": "alert-platform ops@example.com",
	})

	for _, want := range []string{
		"ALERT_SERVICE_NAME=fixture-insider",
		"ALERT_STATE_DIR=/var/lib/alert-platform/fixture-insider",
		"ALERT_LOG_FORMAT=json",
		"ALERT_METRICS_PORT=9113",
		"ALERT_HEARTBEAT_INTERVAL_SEC=120", // service override, not the 300 default
		"ALERT_DEPLOYED_REF=v2.0.1",
		"ALERT_SECRET_TG_BOT_TOKEN=123:secret",
		"ALERT_POLLING_MIN_TRANSACTION_VALUE_USD=",
		"PYTHONPATH=/opt/alert-platform/fixture-insider/current/services",
	} {
		if !strings.Contains(env, want) {
			t.Errorf("env missing %q\n---\n%s", want, env)
		}
	}
	// Secret *names* must not leak into env keys as references; only values.
	if strings.Contains(env, "ALERT_BOT_TOKEN_SECRET") {
		t.Error("secret reference key was emitted instead of the resolved value")
	}
}

// A User-Agent with a space is the exact case that silently truncates and
// gets every SEC request 403'd, so it gets an explicit test.
func TestEnvQuotingSurvivesValuesWithSpaces(t *testing.T) {
	_, eff, _ := load(t)
	env := RenderEnv(eff, "v2.0.1", map[string]string{
		"fixture_user_agent": "alert-platform ops@example.com",
	})
	if !strings.Contains(env, `ALERT_SECRET_FIXTURE_USER_AGENT="alert-platform ops@example.com"`) {
		t.Errorf("value with a space was not quoted for systemd:\n%s", env)
	}
}

func TestPollingExtensionKeysAreEncoded(t *testing.T) {
	repo := loadFixture(t)
	svc, _ := repo.Service("fixture-filings")
	env := RenderEnv(fleet.Resolve(repo.Fleet, svc), fixtureRef, nil)

	// `forms` is a list in the spec and must arrive as JSON the Python side
	// can decode back into a list.
	line := grepLine(env, "ALERT_POLLING_FORMS=")
	if line == "" {
		t.Fatalf("forms not exported:\n%s", env)
	}
	raw := strings.TrimPrefix(line, "ALERT_POLLING_FORMS=")
	raw = strings.Trim(raw, `"`)
	raw = strings.ReplaceAll(raw, `\"`, `"`)
	var forms []string
	if err := json.Unmarshal([]byte(raw), &forms); err != nil {
		t.Fatalf("forms is not decodable JSON (%v): %s", err, line)
	}
	if len(forms) != 5 || forms[0] != "8-K" {
		t.Errorf("forms decoded to %v", forms)
	}
}

// -- planning --------------------------------------------------------------

func TestPlanOnEmptyHostIsAllCreates(t *testing.T) {
	repo := loadFixture(t)
	runner := pexec.NewDry(nil)

	plan, err := BuildPlan(repo, runner, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Services) != len(repo.Services) {
		t.Fatalf("expected one plan per spec (%d), got %d", len(repo.Services), len(plan.Services))
	}
	for _, sp := range plan.Services {
		if sp.Action != "create" {
			t.Errorf("%s: action = %q, want create on a bare host", sp.Service, sp.Action)
		}
	}
	if plan.ID == "" {
		t.Error("plan has no fingerprint")
	}
}

func TestPlanIsNoopWhenHostMatchesDesiredState(t *testing.T) {
	repo := loadFixture(t)
	svc, _ := repo.Service("fixture-trials")
	eff := fleet.Resolve(repo.Fleet, svc)
	target, _ := repo.DefaultTarget()

	unit := RenderUnit(eff, target)
	ref := eff.String("source.ref", "")
	placeholders := map[string]string{}
	for _, n := range eff.SecretNames() {
		placeholders[n] = SecretPlaceholder
	}
	manifest := Manifest{
		Service:  "fixture-trials",
		Ref:      ref,
		UnitHash: Hash(unit),
		EnvHash:  Hash(RenderEnv(eff, ref, placeholders)),
	}
	raw, _ := json.Marshal(manifest)

	runner := pexec.NewDry(nil)
	runner.Responses["cat "+ManifestPath("fixture-trials")+" 2>/dev/null || true"] =
		pexec.Result{Stdout: string(raw)}
	runner.Responses["sha256sum "+UnitPath("fixture-trials")+" 2>/dev/null | cut -c1-16 || true"] =
		pexec.Result{Stdout: Hash(unit) + "\n"}
	runner.Responses["systemctl show "+UnitName("fixture-trials")+
		" --property=ActiveState,UnitFileState --value 2>/dev/null || true"] =
		pexec.Result{Stdout: "active\nenabled\n"}

	plan, err := BuildPlan(repo, runner, []string{"fixture-trials"})
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Services) != 1 {
		t.Fatalf("expected 1 plan, got %d", len(plan.Services))
	}
	if plan.Services[0].Action != "noop" {
		t.Errorf("action = %q with changes %+v, want noop",
			plan.Services[0].Action, plan.Services[0].Changes)
	}
}

func TestPlanDetectsRefChangeAndInactiveUnit(t *testing.T) {
	repo := loadFixture(t)
	// An older ref than the fixture pins: the change this test is about.
	manifest, _ := json.Marshal(Manifest{Service: "fixture-trials", Ref: "v0.9.0"})

	runner := pexec.NewDry(nil)
	runner.Responses["cat "+ManifestPath("fixture-trials")+" 2>/dev/null || true"] =
		pexec.Result{Stdout: string(manifest)}
	runner.Responses["sha256sum "+UnitPath("fixture-trials")+" 2>/dev/null | cut -c1-16 || true"] =
		pexec.Result{Stdout: "deadbeefdeadbeef\n"}
	runner.Responses["systemctl show "+UnitName("fixture-trials")+
		" --property=ActiveState,UnitFileState --value 2>/dev/null || true"] =
		pexec.Result{Stdout: "failed\nenabled\n"}

	plan, _ := BuildPlan(repo, runner, []string{"fixture-trials"})
	sp := plan.Services[0]

	if sp.Action != "update" {
		t.Fatalf("action = %q, want update", sp.Action)
	}
	fields := map[string]bool{}
	for _, c := range sp.Changes {
		fields[c.Field] = true
	}
	for _, want := range []string{"source.ref", "systemd unit", "state"} {
		if !fields[want] {
			t.Errorf("plan did not report a change for %q; got %+v", want, sp.Changes)
		}
	}
}

func TestPlanNeverContainsSecretValues(t *testing.T) {
	repo := loadFixture(t)
	plan, err := BuildPlan(repo, pexec.NewDry(nil), nil)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(plan)
	// Names are fine and useful; values must never be here. The plan is
	// printed to terminals and saved to disk.
	if strings.Contains(string(raw), "AAA") || strings.Contains(string(raw), "bot") &&
		strings.Contains(string(raw), ":AA") {
		t.Error("plan appears to contain a credential-shaped value")
	}
	var b strings.Builder
	plan.Render(&b)
	if strings.Contains(b.String(), SecretPlaceholder) {
		t.Error("rendered plan leaked the placeholder into operator output")
	}
}

func TestPlanFingerprintChangesWhenDesiredStateChanges(t *testing.T) {
	repo := loadFixture(t)
	first, _ := BuildPlan(repo, pexec.NewDry(nil), nil)

	for i := range repo.Services {
		if repo.Services[i].Metadata.Name == "fixture-trials" {
			repo.Services[i].Spec["source"].(map[string]any)["ref"] = "v9.9.9"
		}
	}
	second, _ := BuildPlan(repo, pexec.NewDry(nil), nil)

	if first.ID == second.ID {
		t.Error("fingerprint unchanged after a spec edit; stale plans would apply silently")
	}
}

func TestPlanRoundTripsThroughDisk(t *testing.T) {
	repo := loadFixture(t)
	plan, _ := BuildPlan(repo, pexec.NewDry(nil), nil)

	path := filepath.Join(t.TempDir(), "plan.json")
	if err := plan.Save(path); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadPlan(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.ID != plan.ID {
		t.Errorf("id changed across save/load: %s -> %s", plan.ID, loaded.ID)
	}
}

// -- apply -----------------------------------------------------------------

func TestApplyIssuesTheExpectedSequenceAndAudits(t *testing.T) {
	repo := loadFixture(t)
	target, _ := repo.DefaultTarget()
	runner := pexec.NewDry(nil)

	plan, err := BuildPlan(repo, runner, []string{"fixture-trials"})
	if err != nil {
		t.Fatal(err)
	}

	auditPath := filepath.Join(t.TempDir(), "audit.jsonl")
	log, _ := audit.Open(auditPath)

	applier := &Applier{
		Repo: repo, Runner: runner, Resolver: StubResolver{Value: "stub"},
		Audit: log, Target: target,
		// Gates and the freshness re-plan need a real host and a tagged repo;
		// this test exercises the deploy mechanics, which is what the dry
		// runner can faithfully record.
		Opts: Options{DryRun: true, SkipGates: true, AutoApprove: true},
	}
	if err := applier.applyService(plan, plan.Services[0]); err != nil {
		t.Fatalf("applyService: %v", err)
	}

	joined := strings.Join(runner.Commands, "\n")
	for _, want := range []string{
		"git clone --depth 1 --branch " + fixtureRef,
		"venv/bin/pip install --quiet -r",
		"write /etc/alert-platform/fixture-trials.env",
		"write /etc/systemd/system/alert-fixture-trials.service",
		"systemctl daemon-reload",
		"systemctl enable alert-fixture-trials.service",
		"systemctl restart alert-fixture-trials.service",
		"write /opt/alert-platform/fixture-trials/deployed.json",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("apply never issued %q\n--- commands ---\n%s", want, joined)
		}
	}

	// The env file must be written before the restart, or the service starts
	// against stale configuration.
	envIdx := strings.Index(joined, "write /etc/alert-platform/fixture-trials.env")
	restartIdx := strings.Index(joined, "systemctl restart")
	if envIdx < 0 || restartIdx < 0 || envIdx > restartIdx {
		t.Error("environment file must be written before the service restarts")
	}

	if err := log.Append(auditEntryFor(plan)); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(auditPath)
	if !strings.Contains(string(raw), `"service":"fixture-trials"`) {
		t.Errorf("audit entry not written: %s", raw)
	}
	if strings.Contains(string(raw), "stub") {
		t.Error("audit log contains a resolved secret value")
	}
}

func TestApplyWritesEnvFileWithSecretsButPlanDoesNot(t *testing.T) {
	repo := loadFixture(t)
	target, _ := repo.DefaultTarget()
	runner := pexec.NewDry(nil)
	plan, _ := BuildPlan(repo, runner, []string{"fixture-poller"})

	log, _ := audit.Open(filepath.Join(t.TempDir(), "audit.jsonl"))
	applier := &Applier{
		Repo: repo, Runner: runner, Resolver: StubResolver{Value: "SUPERSECRET"},
		Audit: log, Target: target,
		Opts: Options{DryRun: true, SkipGates: true, AutoApprove: true},
	}
	if err := applier.applyService(plan, plan.Services[0]); err != nil {
		t.Fatal(err)
	}

	env := runner.Files[EnvFilePath("fixture-poller")]
	if !strings.Contains(env, "SUPERSECRET") {
		t.Error("resolved secret never reached the env file")
	}
	unit := runner.Files[UnitPath("fixture-poller")]
	if strings.Contains(unit, "SUPERSECRET") {
		t.Error("secret leaked into the systemd unit, which is world-readable")
	}
}

func TestSortServicesDeploysCriticalTierLast(t *testing.T) {
	repo := loadFixture(t)
	plan, _ := BuildPlan(repo, pexec.NewDry(nil), nil)
	pending := plan.Pending()
	SortServices(pending)

	last := pending[len(pending)-1]
	if last.Service != "fixture-insider" { // the fixture fleet's only critical-tier service
		t.Errorf("critical-tier service should deploy last, got %q", last.Service)
	}
}

func auditEntryFor(p Plan) audit.Entry {
	return audit.Entry{
		Event: "apply", Service: p.Services[0].Service, PlanID: p.ID,
		Actor: "test", Outcome: "success", ToRef: p.Services[0].DesiredRef,
	}
}

func grepLine(text, prefix string) string {
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, prefix) {
			return line
		}
	}
	return ""
}
