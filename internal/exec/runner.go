// Package exec is the seam between the control plane and the host it acts
// on. Every command the apply engine issues goes through a Runner, which is
// what makes `--dry-run` honest (it records exactly what would run) and what
// makes the engine testable without an EC2 instance.
package exec

import (
	"bytes"
	"fmt"
	"os"
	osexec "os/exec"
	"strings"
	"time"
)

type Result struct {
	Command  string
	Stdout   string
	Stderr   string
	ExitCode int
	Duration time.Duration
}

func (r Result) OK() bool { return r.ExitCode == 0 }

type Runner interface {
	// Run executes a command on the target and returns its result. A
	// non-zero exit is returned in Result, not as an error; err is reserved
	// for the command not running at all (connection failure, timeout).
	Run(cmd string) (Result, error)

	// WriteFile places content at path with the given mode. Separate from
	// Run because writing a rendered unit file through a shell heredoc is
	// how you get quoting bugs in production.
	WriteFile(path, content string, mode os.FileMode) error

	// Describe names the target for logs and audit entries.
	Describe() string
}

// -- SSH -------------------------------------------------------------------

// SSHRunner acts on a remote host via the system ssh client.
//
// Using the ssh binary rather than a Go SSH library is deliberate: it
// inherits the operator's existing ~/.ssh/config, agent, jump hosts and
// known_hosts. The fleet spec says `address: ec2-alerts-prod  # resolved via
// SSH config, not raw IP`, and honouring that means using the same client
// the operator already uses.
type SSHRunner struct {
	Host    string
	User    string
	Timeout time.Duration
}

func NewSSH(host, user string) *SSHRunner {
	return &SSHRunner{Host: host, User: user, Timeout: 5 * time.Minute}
}

func (s *SSHRunner) target() string {
	if s.User == "" {
		return s.Host
	}
	return s.User + "@" + s.Host
}

func (s *SSHRunner) Run(cmd string) (Result, error) {
	args := []string{
		"-o", "BatchMode=yes",
		"-o", "ConnectTimeout=10",
		s.target(), cmd,
	}
	res, err := runLocal("ssh", args, s.Timeout, cmd)
	return checkSSHResult(res, err, s.Describe())
}

// checkSSHResult enforces the Runner contract at the ssh boundary. ssh
// reserves exit 255 for its own failures (DNS, refused, auth) rather than
// the remote command's exit code, so 255 must surface as an error —
// otherwise an unreachable host reads as an empty one and plan proposes
// creating a fleet that already exists.
func checkSSHResult(res Result, err error, target string) (Result, error) {
	if err == nil && res.ExitCode == 255 {
		return res, fmt.Errorf("ssh to %s failed: %s",
			target, strings.TrimSpace(res.Stderr))
	}
	return res, err
}

func (s *SSHRunner) WriteFile(path, content string, mode os.FileMode) error {
	// Write to a temp path then move into place: a half-written unit file
	// that systemd reloads is worse than no change at all.
	tmp := fmt.Sprintf("/tmp/.alertctl-%d", time.Now().UnixNano())
	// The remote path is a platform-generated temp name and is shell-quoted;
	// the host comes from the fleet spec, not from user input.
	cmd := osexec.Command("ssh", "-o", "BatchMode=yes", s.target(), //nolint:gosec
		fmt.Sprintf("cat > %s", shellQuote(tmp)))
	cmd.Stdin = strings.NewReader(content)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("stream file to %s: %w: %s", s.Describe(), err, stderr.String())
	}
	move := fmt.Sprintf("sudo install -m %o %s %s && rm -f %s",
		mode.Perm(), shellQuote(tmp), shellQuote(path), shellQuote(tmp))
	res, err := s.Run(move)
	if err != nil {
		return err
	}
	if !res.OK() {
		return fmt.Errorf("install %s: exit %d: %s", path, res.ExitCode, res.Stderr)
	}
	return nil
}

func (s *SSHRunner) Describe() string { return s.target() }

// -- Local -----------------------------------------------------------------

// LocalRunner executes on this machine, optionally rooted at a directory.
// Used by the integration tests and by `--target local` for a single-host
// setup where the control plane runs on the box it manages.
type LocalRunner struct {
	Root    string
	Timeout time.Duration
}

func NewLocal(root string) *LocalRunner {
	return &LocalRunner{Root: root, Timeout: 5 * time.Minute}
}

func (l *LocalRunner) Run(cmd string) (Result, error) {
	return runLocal("sh", []string{"-c", cmd}, l.Timeout, cmd)
}

func (l *LocalRunner) WriteFile(path, content string, mode os.FileMode) error {
	if l.Root != "" {
		full := l.resolve(path)
		if err := os.MkdirAll(dir(full), 0o755); err != nil {
			return err
		}
		return os.WriteFile(full, []byte(content), mode)
	}
	tmp, err := os.CreateTemp("", ".alertctl-*")
	if err != nil {
		return fmt.Errorf("stage %s: %w", path, err)
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.WriteString(content); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	res, err := l.Run(fmt.Sprintf("sudo install -m %o %s %s",
		mode.Perm(), shellQuote(tmp.Name()), shellQuote(path)))
	if err != nil {
		return err
	}
	if !res.OK() {
		return fmt.Errorf("install %s: exit %d: %s",
			path, res.ExitCode, strings.TrimSpace(res.Stderr))
	}
	return nil
}

func (l *LocalRunner) resolve(path string) string {
	if l.Root == "" {
		return path
	}
	return l.Root + path
}

func (l *LocalRunner) Describe() string {
	if l.Root == "" {
		return "localhost"
	}
	return "localhost:" + l.Root
}

// -- Dry run ---------------------------------------------------------------

// DryRunner records commands without executing them. `alertctl apply
// --dry-run` uses it so an operator can see the exact command sequence
// before letting it near production.
type DryRunner struct {
	Inner    Runner
	Commands []string
	Files    map[string]string
	// Responses lets tests stub observed state for specific commands.
	Responses map[string]Result
}

func NewDry(inner Runner) *DryRunner {
	return &DryRunner{Inner: inner, Files: map[string]string{}, Responses: map[string]Result{}}
}

func (d *DryRunner) Run(cmd string) (Result, error) {
	d.Commands = append(d.Commands, cmd)
	if res, ok := d.Responses[cmd]; ok {
		return res, nil
	}
	// Read-only probes are safe to run for real when an inner runner exists;
	// anything mutating is recorded and skipped.
	if d.Inner != nil && isReadOnly(cmd) {
		return d.Inner.Run(cmd)
	}
	return Result{Command: cmd, ExitCode: 0}, nil
}

func (d *DryRunner) WriteFile(path, content string, _ os.FileMode) error {
	d.Files[path] = content
	d.Commands = append(d.Commands, "write "+path)
	return nil
}

func (d *DryRunner) Describe() string {
	if d.Inner != nil {
		return "dry-run(" + d.Inner.Describe() + ")"
	}
	return "dry-run"
}

// isReadOnly decides whether a dry run may execute a command for real.
//
// A prefix check alone is not enough, and getting this wrong is not a
// cosmetic bug: `test -d X || sudo git clone ...` starts with a read-only
// prefix and ends by mutating the host, which would make `--dry-run` a lie.
// So a command qualifies only if it starts with a known-safe verb AND
// contains no shell chaining and no privilege escalation.
func isReadOnly(cmd string) bool {
	trimmed := strings.TrimSpace(cmd)

	for _, token := range []string{"||", "&&", ";", "|", ">", "sudo"} {
		if strings.Contains(trimmed, token) {
			return false
		}
	}
	for _, prefix := range []string{
		"systemctl show", "systemctl is-active", "systemctl status",
		"cat ", "ls ", "curl ", "sha256sum ", "journalctl ",
	} {
		if strings.HasPrefix(trimmed, prefix) {
			return true
		}
	}
	return false
}

// -- helpers ---------------------------------------------------------------

func runLocal(bin string, args []string, timeout time.Duration, label string) (Result, error) {
	start := time.Now()
	cmd := osexec.Command(bin, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	done := make(chan error, 1)
	if err := cmd.Start(); err != nil {
		return Result{Command: label}, err
	}
	go func() { done <- cmd.Wait() }()

	select {
	case err := <-done:
		res := Result{
			Command:  label,
			Stdout:   stdout.String(),
			Stderr:   stderr.String(),
			Duration: time.Since(start),
		}
		if exitErr, ok := err.(*osexec.ExitError); ok {
			res.ExitCode = exitErr.ExitCode()
			return res, nil
		}
		if err != nil {
			return res, err
		}
		return res, nil
	case <-time.After(timeout):
		_ = cmd.Process.Kill()
		return Result{Command: label, ExitCode: -1, Duration: time.Since(start)},
			fmt.Errorf("command timed out after %s: %s", timeout, label)
	}
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

func dir(path string) string {
	i := strings.LastIndex(path, "/")
	if i <= 0 {
		return "/"
	}
	return path[:i]
}
