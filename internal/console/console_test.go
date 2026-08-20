package console

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/conanohara/alert-platform/internal/audit"
	pexec "github.com/conanohara/alert-platform/internal/exec"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "..", "..")
}

func testServer(t *testing.T) (*Server, *httptest.Server) {
	t.Helper()
	s := &Server{
		Root:      repoRoot(t),
		Runner:    pexec.NewDry(nil),
		AuditPath: filepath.Join(t.TempDir(), "audit.jsonl"),
	}
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(ts.Close)
	return s, ts
}

func get(t *testing.T, url string, out any) *http.Response {
	t.Helper()
	res, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { res.Body.Close() })
	if out != nil {
		if err := json.NewDecoder(res.Body).Decode(out); err != nil {
			t.Fatalf("decode %s: %v", url, err)
		}
	}
	return res
}

// The overview is the console's offline guarantee: it comes from the specs
// alone and must render every managed service with no target at all.
func TestOverviewComesFromSpecsAndNeedsNoTarget(t *testing.T) {
	_, ts := testServer(t)

	var out struct {
		Fleet    struct{ Name string }
		Services []struct {
			Name, Tier, Ref string
			MetricsPort     int `json:"metrics_port"`
			Secrets         []string
		}
	}
	res := get(t, ts.URL+"/api/overview", &out)
	if res.StatusCode != 200 {
		t.Fatalf("overview: %d", res.StatusCode)
	}
	if out.Fleet.Name != "market-alert-suite" {
		t.Errorf("fleet name = %q", out.Fleet.Name)
	}
	if len(out.Services) != 4 {
		t.Fatalf("want 4 services, got %d", len(out.Services))
	}
	ports := map[int]bool{}
	for _, svc := range out.Services {
		if svc.Ref == "" || svc.Tier == "" {
			t.Errorf("%s: overview must carry ref and tier from the effective config", svc.Name)
		}
		if ports[svc.MetricsPort] {
			t.Errorf("duplicate metrics port %d — the fleet invariant should make this impossible", svc.MetricsPort)
		}
		ports[svc.MetricsPort] = true
		for _, secret := range svc.Secrets {
			if strings.Contains(secret, ":") || len(secret) > 64 {
				t.Errorf("%s: %q does not look like a secret NAME; values must never reach the console", svc.Name, secret)
			}
		}
	}
}

// The console is a view, not a control surface. Anything that is not a read
// must be refused at the boundary, so that a future mutating endpoint is an
// explicit decision rather than something a handler grew by accident.
func TestConsoleRefusesNonReadMethods(t *testing.T) {
	_, ts := testServer(t)
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete} {
		req, _ := http.NewRequest(method, ts.URL+"/api/plan", nil)
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		res.Body.Close()
		if res.StatusCode != http.StatusMethodNotAllowed {
			t.Errorf("%s must be refused, got %d", method, res.StatusCode)
		}
	}
}

func TestAuditEndpointServesNewestFirst(t *testing.T) {
	s, ts := testServer(t)
	log, _ := audit.Open(s.AuditPath)
	for _, ref := range []string{"v0.1.0", "v0.2.0"} {
		if err := log.Append(audit.Entry{Event: "apply", Service: "edgar-mna", ToRef: ref, Outcome: "success"}); err != nil {
			t.Fatal(err)
		}
	}

	var out struct {
		Entries []struct {
			ToRef string `json:"to_ref"`
		}
	}
	get(t, ts.URL+"/api/audit?n=10", &out)
	if len(out.Entries) != 2 {
		t.Fatalf("want 2 entries, got %d", len(out.Entries))
	}
	if out.Entries[0].ToRef != "v0.2.0" {
		t.Errorf("console must show the most recent deploy first, got %q", out.Entries[0].ToRef)
	}
}

// A dry runner with no inner runner observes nothing — the plan endpoint
// must still answer (all creates), because "target says nothing" is a state
// the console has to render, not an internal error.
func TestPlanEndpointAnswersAgainstAnEmptyTarget(t *testing.T) {
	_, ts := testServer(t)
	var out struct {
		ID       string
		Services []struct{ Action string }
	}
	res := get(t, ts.URL+"/api/plan", &out)
	if res.StatusCode != 200 {
		t.Fatalf("plan: %d", res.StatusCode)
	}
	if out.ID == "" {
		t.Error("plan must carry its fingerprint; the UI shows it so operators can match a saved plan.json")
	}
	for _, svc := range out.Services {
		if svc.Action != "create" {
			t.Errorf("an empty target should plan creates, got %q", svc.Action)
		}
	}
}

func TestIndexServesTheEmbeddedConsole(t *testing.T) {
	_, ts := testServer(t)
	res, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	buf := make([]byte, 4096)
	n, _ := res.Body.Read(buf)
	if !strings.Contains(string(buf[:n]), "alert-platform console") {
		t.Error("embedded index.html did not serve")
	}
}
