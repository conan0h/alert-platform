// Package audit writes the deploy history.
//
// The log is the answer to "what changed, when, by whom, and did it work?"
// — the first question asked in every incident. It is append-only JSONL so
// it can be tailed, grepped, and shipped without a parser, and it lives on
// the target host (fleet.change_policy.audit_log) so it survives the
// operator's laptop.
//
// Secret values never enter it. Secret names do, because knowing that a
// deploy rotated `tg_bot_token` is operationally useful and knowing its
// value is not.
package audit

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type Entry struct {
	Timestamp time.Time      `json:"ts"`
	Event     string         `json:"event"`
	Service   string         `json:"service,omitempty"`
	PlanID    string         `json:"plan_id,omitempty"`
	Actor     string         `json:"actor"`
	Target    string         `json:"target,omitempty"`
	FromRef   string         `json:"from_ref,omitempty"`
	ToRef     string         `json:"to_ref,omitempty"`
	Outcome   string         `json:"outcome,omitempty"`
	Duration  string         `json:"duration,omitempty"`
	Detail    map[string]any `json:"detail,omitempty"`
}

type Log struct {
	Path string
}

func Open(path string) (*Log, error) {
	if path == "" {
		return nil, fmt.Errorf("audit log path is empty; set change_policy.audit_log")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("create audit log dir: %w", err)
	}
	return &Log{Path: path}, nil
}

// Append writes one entry. A failure here fails the deploy: an unlogged
// deploy is worse than no deploy, because the next operator will trust the
// log and be wrong.
func (l *Log) Append(e Entry) error {
	if e.Timestamp.IsZero() {
		e.Timestamp = time.Now().UTC()
	}
	raw, err := json.Marshal(e)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(l.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
	if err != nil {
		return fmt.Errorf("open audit log: %w", err)
	}
	defer f.Close()
	if _, err := f.Write(append(raw, '\n')); err != nil {
		return fmt.Errorf("write audit entry: %w", err)
	}
	return f.Sync()
}

// History returns entries for a service, newest last.
func (l *Log) History(service string) ([]Entry, error) {
	raw, err := os.ReadFile(l.Path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []Entry
	for _, line := range splitLines(raw) {
		if len(line) == 0 {
			continue
		}
		var e Entry
		if err := json.Unmarshal(line, &e); err != nil {
			continue // a corrupt line must not hide the rest of the history
		}
		if service == "" || e.Service == service {
			out = append(out, e)
		}
	}
	return out, nil
}

// LastSuccessfulRef finds the ref a service was last known-good at, which
// is what `alertctl rollback` targets when no explicit ref is given.
func (l *Log) LastSuccessfulRef(service, excluding string) (string, error) {
	entries, err := l.History(service)
	if err != nil {
		return "", err
	}
	for i := len(entries) - 1; i >= 0; i-- {
		e := entries[i]
		if e.Event == "apply" && e.Outcome == "success" && e.ToRef != "" && e.ToRef != excluding {
			return e.ToRef, nil
		}
	}
	return "", fmt.Errorf("no successful deploy of %q found in %s other than %q",
		service, l.Path, excluding)
}

func splitLines(raw []byte) [][]byte {
	var out [][]byte
	start := 0
	for i, b := range raw {
		if b == '\n' {
			out = append(out, raw[start:i])
			start = i + 1
		}
	}
	if start < len(raw) {
		out = append(out, raw[start:])
	}
	return out
}
