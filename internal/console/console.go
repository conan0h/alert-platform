// Package console serves a read-only web view of the platform.
//
// It is deliberately a second interface over the same code paths the CLI
// uses — fleet.Load, engine.Observe, engine.BuildPlan, audit.History — and
// introduces no state of its own. If the console and the CLI ever disagree,
// that is a bug in one of them, not a synchronization problem, because
// there is nothing to synchronize.
//
// Two properties are load-bearing:
//
//   - Read-only by construction. Every endpoint is a GET and none of them
//     reach a code path that mutates the target. There is no apply button:
//     mutations stay in the CLI, where the plan-review, confirmation and
//     audit flow lives.
//   - Degrades to the specs. Desired state comes from the repo and renders
//     with no target at all; observed state (status, plan, drift) appears
//     when the target is reachable and turns into an explicit banner when
//     it is not. The console never invents a healthy-looking fleet.
//
// It binds to loopback by default. For a remote host, port-forward it the
// same way the platform already talks to the host:
//
//	ssh -L 8600:127.0.0.1:8600 ec2-alerts-prod
package console

import (
	"embed"
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/conanohara/alert-platform/internal/audit"
	"github.com/conanohara/alert-platform/internal/engine"
	pexec "github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

//go:embed assets/index.html
var assets embed.FS

// Server holds what the console needs to answer requests. Specs are
// re-loaded from Root on each request so an edited spec shows up on
// refresh; observed state is cached briefly (see cache) so a busy dashboard
// does not hammer an SSH target.
type Server struct {
	Root      string
	Runner    pexec.Runner
	AuditPath string

	statusCache cache[[]statusRow]
	planCache   cache[planView]
}

// cache is a single-value TTL cache. Observing four services over SSH costs
// ~a second; a dashboard polling every few seconds should not multiply that
// onto the host.
type cache[T any] struct {
	mu    sync.Mutex
	at    time.Time
	value T
	err   error
}

func (c *cache[T]) get(ttl time.Duration, fill func() (T, error)) (T, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if time.Since(c.at) < ttl {
		return c.value, c.err
	}
	c.value, c.err = fill()
	c.at = time.Now()
	return c.value, c.err
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.index)
	mux.HandleFunc("/api/overview", s.apiOverview)
	mux.HandleFunc("/api/status", s.apiStatus)
	mux.HandleFunc("/api/plan", s.apiPlan)
	mux.HandleFunc("/api/audit", s.apiAudit)
	return readOnly(mux)
}

// readOnly rejects anything that is not a GET or HEAD. The console has no
// mutating endpoints to protect, but enforcing the method at the boundary
// means adding one later is an explicit decision, not an accident.
func readOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			http.Error(w, "the console is read-only; changes go through `alertctl plan` and `alertctl apply`",
				http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("X-Content-Type-Options", "nosniff")
		next.ServeHTTP(w, r)
	})
}

func (s *Server) index(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	raw, err := assets.ReadFile("assets/index.html")
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(raw)
}

// -- /api/overview: desired state, straight from the specs -----------------

type serviceView struct {
	Name        string   `json:"name"`
	Tier        string   `json:"tier"`
	Description string   `json:"description"`
	Ref         string   `json:"ref"`
	Unit        string   `json:"unit"`
	SourcePath  string   `json:"source_path"`
	IntervalSec int      `json:"poll_interval_sec"`
	MetricsPort int      `json:"metrics_port"`
	Heartbeat   int      `json:"heartbeat_interval_sec"`
	Grace       int      `json:"startup_grace_sec"`
	StateDir    string   `json:"state_dir"`
	Secrets     []string `json:"secrets"`
}

type overview struct {
	Fleet        fleet.FleetMetadata `json:"fleet"`
	Target       fleet.Target        `json:"target"`
	TargetMode   string              `json:"target_mode"`
	ChangePolicy fleet.ChangePolicy  `json:"change_policy"`
	Services     []serviceView       `json:"services"`
	GeneratedAt  time.Time           `json:"generated_at"`
}

func (s *Server) apiOverview(w http.ResponseWriter, _ *http.Request) {
	repo, err := fleet.Load(s.Root)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errBody(err))
		return
	}
	target, err := repo.DefaultTarget()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errBody(err))
		return
	}

	out := overview{
		Fleet:        repo.Fleet.Metadata,
		Target:       target,
		TargetMode:   s.Runner.Describe(),
		ChangePolicy: repo.Fleet.ChangePolicy,
		GeneratedAt:  time.Now().UTC(),
	}
	for _, svc := range repo.Services {
		eff := fleet.Resolve(repo.Fleet, svc)
		name := svc.Metadata.Name
		out.Services = append(out.Services, serviceView{
			Name:        name,
			Tier:        eff.Tier,
			Description: eff.Description,
			Ref:         eff.String("source.ref", ""),
			Unit:        engine.UnitName(name),
			SourcePath:  eff.String("source.path", ""),
			IntervalSec: eff.Int("polling.interval_sec", 0),
			MetricsPort: eff.Int("health.metrics.port", 0),
			Heartbeat:   eff.Int("health.heartbeat_interval_sec", 0),
			Grace:       eff.Int("health.startup_grace_sec", 0),
			StateDir:    eff.String("state.dir", "") + "/" + name,
			Secrets:     eff.SecretNames(),
		})
	}
	writeJSON(w, http.StatusOK, out)
}

// -- /api/status: what the target reports right now ------------------------

type statusRow struct {
	Service    string `json:"service"`
	Ref        string `json:"ref"`
	State      string `json:"state"`
	Enabled    string `json:"enabled"`
	DeployedAt string `json:"deployed_at,omitempty"`
	DeployedBy string `json:"deployed_by,omitempty"`
	PlanID     string `json:"plan_id,omitempty"`
	Error      string `json:"error,omitempty"`
}

func (s *Server) apiStatus(w http.ResponseWriter, _ *http.Request) {
	rows, err := s.statusCache.get(5*time.Second, func() ([]statusRow, error) {
		repo, err := fleet.Load(s.Root)
		if err != nil {
			return nil, err
		}
		var out []statusRow
		for _, svc := range repo.Services {
			name := svc.Metadata.Name
			row := statusRow{Service: name}
			obs, err := engine.Observe(s.Runner, name)
			if err != nil {
				// One unreachable probe must not blank the whole table:
				// report it in the row and keep going.
				row.Error = err.Error()
				out = append(out, row)
				continue
			}
			row.State, row.Enabled = obs.Active, obs.Enabled
			if obs.Exists {
				row.Ref = obs.Manifest.Ref
				row.DeployedAt = obs.Manifest.DeployedAt
				row.DeployedBy = obs.Manifest.DeployedBy
				row.PlanID = obs.Manifest.PlanID
			}
			out = append(out, row)
		}
		return out, nil
	})
	if err != nil {
		writeJSON(w, http.StatusBadGateway, errBody(err))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"services": rows})
}

// -- /api/plan: the live diff between desired and observed -----------------

type planView struct {
	ID        string               `json:"id"`
	CreatedAt time.Time            `json:"created_at"`
	Target    string               `json:"target"`
	Services  []engine.ServicePlan `json:"services"`
}

func (s *Server) apiPlan(w http.ResponseWriter, _ *http.Request) {
	view, err := s.planCache.get(5*time.Second, func() (planView, error) {
		repo, err := fleet.Load(s.Root)
		if err != nil {
			return planView{}, err
		}
		// BuildPlan is read-only by construction (it only ever calls
		// Observe), which is what makes it safe to run on every refresh.
		plan, err := engine.BuildPlan(repo, s.Runner, nil)
		if err != nil {
			return planView{}, err
		}
		return planView{
			ID: plan.ID, CreatedAt: plan.CreatedAt,
			Target: plan.Target, Services: plan.Services,
		}, nil
	})
	if err != nil {
		writeJSON(w, http.StatusBadGateway, errBody(err))
		return
	}
	writeJSON(w, http.StatusOK, view)
}

// -- /api/audit: the deploy record ------------------------------------------

func (s *Server) apiAudit(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if n := r.URL.Query().Get("n"); n != "" {
		if _, err := jsonNumber(n, &limit); err != nil {
			writeJSON(w, http.StatusBadRequest, errBody(err))
			return
		}
	}
	log, err := audit.Open(s.AuditPath)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errBody(err))
		return
	}
	entries, err := log.History(r.URL.Query().Get("service"))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errBody(err))
		return
	}
	if len(entries) > limit {
		entries = entries[len(entries)-limit:]
	}
	// Newest first: the console answers "what just happened?", not
	// "how did we get here?" — that reading order belongs to `history`.
	for i, j := 0, len(entries)-1; i < j; i, j = i+1, j-1 {
		entries[i], entries[j] = entries[j], entries[i]
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries, "path": log.Path})
}

// -- helpers ---------------------------------------------------------------

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func errBody(err error) map[string]string {
	return map[string]string{"error": err.Error()}
}

func jsonNumber(s string, out *int) (int, error) {
	err := json.Unmarshal([]byte(s), out)
	return *out, err
}
