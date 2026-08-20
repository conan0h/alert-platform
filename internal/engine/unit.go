package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/conanohara/alert-platform/internal/fleet"
)

// Paths on the target host. Every one is derived from metadata.name, which
// is why renaming a service is defined as delete-plus-create (docs/spec.md,
// D1) rather than an in-place edit.
const (
	InstallRoot = "/opt/alert-platform"
	UnitDir     = "/etc/systemd/system"
	EnvDir      = "/etc/alert-platform"
)

func UnitName(service string) string    { return "alert-" + service + ".service" }
func UnitPath(service string) string    { return UnitDir + "/" + UnitName(service) }
func ServiceRoot(service string) string { return InstallRoot + "/" + service }
func ReleaseDir(service, ref string) string {
	return ServiceRoot(service) + "/releases/" + ref
}
func CurrentLink(service string) string  { return ServiceRoot(service) + "/current" }
func EnvFilePath(service string) string  { return EnvDir + "/" + service + ".env" }
func ManifestPath(service string) string { return ServiceRoot(service) + "/deployed.json" }

// Manifest is what the host records about its own state. `plan` reads it to
// answer "what is actually deployed?" without trusting the git checkout or
// the operator's memory.
type Manifest struct {
	Service    string `json:"service"`
	Ref        string `json:"ref"`
	UnitHash   string `json:"unit_hash"`
	EnvHash    string `json:"env_hash"`
	DeployedAt string `json:"deployed_at"`
	DeployedBy string `json:"deployed_by"`
	PlanID     string `json:"plan_id"`
}

// RenderUnit produces the systemd unit for a service from its effective
// config. Nothing here is hand-written per service: the unit is a pure
// function of desired state, which is what makes drift detectable — a unit
// on disk that differs from this output is, by definition, drift.
func RenderUnit(e fleet.Effective, target fleet.Target) string {
	var b strings.Builder

	name := e.Name
	user := e.String("runtime.user", "svc-alerts")
	workingDir := CurrentLink(name)
	interpreter := e.String("runtime.interpreter", "python3.12")
	entrypoint := e.String("source.entrypoint", "main.py")
	sourcePath := e.String("source.path", "services/"+strings.ReplaceAll(name, "-", "_"))
	restart := e.String("runtime.restart_policy", "on-failure")
	restartSec := e.Int("runtime.restart_sec", 10)
	memoryMax := e.String("resources.memory_max", e.String("runtime.memory_max", "256M"))
	stateDir := e.String("state.dir", "/var/lib/alert-platform") + "/" + name

	fmt.Fprintf(&b, "# Managed by alertctl. Do not edit on the host.\n")
	fmt.Fprintf(&b, "# Generated from fleet/services/%s.yaml — edit the spec and re-apply.\n", name)
	fmt.Fprintf(&b, "# Target: %s\n\n", target.Address)

	b.WriteString("[Unit]\n")
	fmt.Fprintf(&b, "Description=%s\n", e.Description)
	b.WriteString("After=network-online.target\n")
	b.WriteString("Wants=network-online.target\n")
	b.WriteString("StartLimitIntervalSec=300\n")
	// Five restarts in five minutes is a crash loop, not a flap. Letting
	// systemd give up is what makes the post-deploy gate able to observe a
	// failed deploy instead of watching a unit restart forever.
	b.WriteString("StartLimitBurst=5\n\n")

	b.WriteString("[Service]\n")
	b.WriteString("Type=simple\n")
	fmt.Fprintf(&b, "User=%s\n", user)
	fmt.Fprintf(&b, "WorkingDirectory=%s\n", workingDir)
	fmt.Fprintf(&b, "EnvironmentFile=%s\n", EnvFilePath(name))
	fmt.Fprintf(&b, "ExecStart=%s/venv/bin/%s %s/%s/%s\n",
		workingDir, interpreter, workingDir, sourcePath, entrypoint)
	fmt.Fprintf(&b, "Restart=%s\n", restart)
	fmt.Fprintf(&b, "RestartSec=%d\n", restartSec)
	fmt.Fprintf(&b, "MemoryMax=%s\n", memoryMax)

	// Logs go to journald as structured JSON; the service writes stdout.
	b.WriteString("StandardOutput=journal\n")
	b.WriteString("StandardError=journal\n")
	fmt.Fprintf(&b, "SyslogIdentifier=%s\n", name)

	// State directory ownership is systemd's job, not the service's.
	fmt.Fprintf(&b, "StateDirectory=alert-platform/%s\n", name)
	fmt.Fprintf(&b, "ReadWritePaths=%s\n", stateDir)

	// Hardening. These are cheap for a polling process that only needs
	// outbound HTTPS and its own state directory, and they turn a
	// dependency compromise from "root on the box" into "sandboxed process".
	b.WriteString("NoNewPrivileges=true\n")
	b.WriteString("PrivateTmp=true\n")
	b.WriteString("ProtectSystem=strict\n")
	b.WriteString("ProtectHome=true\n")
	b.WriteString("ProtectKernelTunables=true\n")
	b.WriteString("ProtectControlGroups=true\n")
	b.WriteString("RestrictAddressFamilies=AF_INET AF_INET6\n")
	b.WriteString("RestrictSUIDSGID=true\n")
	b.WriteString("LockPersonality=true\n\n")

	b.WriteString("[Install]\n")
	b.WriteString("WantedBy=multi-user.target\n")

	return b.String()
}

// RenderEnv builds the EnvironmentFile: the contract consumed by
// services/alertlib/config.py.
//
// `secrets` maps secret name -> resolved value. Values are written to a
// 0640 file owned by the service user on the host and never appear in the
// repo, the plan, or the audit log.
func RenderEnv(e fleet.Effective, ref string, secrets map[string]string) string {
	var b strings.Builder
	b.WriteString("# Managed by alertctl. Regenerated on every apply.\n")
	b.WriteString("# Contains resolved secret values — never copy this file.\n")

	name := e.Name
	stateDir := e.String("state.dir", "/var/lib/alert-platform") + "/" + name

	kv := [][2]string{
		{"ALERT_SERVICE_NAME", name},
		{"ALERT_SERVICE_TIER", e.Tier},
		{"ALERT_STATE_DIR", stateDir},
		{"ALERT_LOG_LEVEL", e.String("logging.level", "INFO")},
		{"ALERT_LOG_FORMAT", e.String("logging.format", "json")},
		{"ALERT_POLL_INTERVAL_SEC", fmt.Sprint(e.Int("polling.interval_sec", 300))},
		{"ALERT_SOURCE_URL", e.String("polling.source_url", "")},
		{"ALERT_HEARTBEAT_INTERVAL_SEC", fmt.Sprint(e.Int("health.heartbeat_interval_sec", 300))},
		{"ALERT_STARTUP_GRACE_SEC", fmt.Sprint(e.Int("health.startup_grace_sec", 60))},
		{"ALERT_METRICS_ENABLED", fmt.Sprint(e.Bool("health.metrics.enabled", true))},
		{"ALERT_METRICS_PORT", fmt.Sprint(e.Int("health.metrics.port", 0))},
		{"ALERT_RATE_LIMIT_PER_MIN", fmt.Sprint(e.Int("delivery.rate_limit_per_min", 20))},
		{"ALERT_DEDUP_RETENTION_DAYS", fmt.Sprint(e.Int("dedup.retention_days", 90))},
		{"ALERT_DEPLOYED_REF", ref},
		{"PYTHONPATH", CurrentLink(name) + "/services"},
		{"PYTHONUNBUFFERED", "1"},
	}
	for _, pair := range kv {
		fmt.Fprintf(&b, "%s=%s\n", pair[0], envQuote(pair[1]))
	}

	// spec.polling extension keys — the designated per-service escape hatch.
	// Non-scalars are JSON so the Python side can round-trip them.
	polling := e.Map("polling")
	for _, key := range fleet.SortedKeys(polling) {
		if key == "interval_sec" || key == "source_url" || strings.HasSuffix(key, "_secret") {
			continue
		}
		fmt.Fprintf(&b, "ALERT_POLLING_%s=%s\n",
			strings.ToUpper(key), envQuote(encodeValue(polling[key])))
	}

	names := make([]string, 0, len(secrets))
	for k := range secrets {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, secretName := range names {
		fmt.Fprintf(&b, "ALERT_SECRET_%s=%s\n",
			strings.ToUpper(secretName), envQuote(secrets[secretName]))
	}
	return b.String()
}

func encodeValue(v any) string {
	switch typed := v.(type) {
	case string:
		return typed
	case bool, int, int64, float64, uint64:
		return fmt.Sprint(typed)
	default:
		raw, err := json.Marshal(v)
		if err != nil {
			return fmt.Sprint(v)
		}
		return string(raw)
	}
}

// envQuote produces a value safe for a systemd EnvironmentFile.
//
// systemd parses these itself (it is not a shell), and unquoted values with
// spaces are truncated at the first space — which is exactly how a User-Agent
// string like "alert-platform ops@example.com" silently becomes
// "alert-platform" and every SEC request starts getting 403s.
func envQuote(v string) string {
	if v == "" {
		return `""`
	}
	needsQuote := strings.ContainsAny(v, " \t\"'$#\\") || strings.Contains(v, "\n")
	if !needsQuote {
		return v
	}
	escaped := strings.ReplaceAll(v, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `"`, `\"`)
	escaped = strings.ReplaceAll(escaped, "\n", `\n`)
	return `"` + escaped + `"`
}

// Hash is used to compare rendered artefacts against what the host reports,
// so drift detection never has to diff whole files across an SSH pipe.
func Hash(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])[:16]
}
