package audit

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The audit log is the platform's answer to "what changed and did it work?"
// These tests pin the three properties that answer depends on: entries are
// only ever appended, a damaged line never hides the rest of the history,
// and rollback target selection skips failures.

func tempLog(t *testing.T) *Log {
	t.Helper()
	log, err := Open(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	return log
}

func TestOpenRefusesAnEmptyPath(t *testing.T) {
	if _, err := Open(""); err == nil {
		t.Fatal("Open(\"\") must fail: an audit log with no destination is a deploy with no record")
	}
}

func TestAppendThenHistoryRoundTrips(t *testing.T) {
	log := tempLog(t)
	in := Entry{
		Event: "apply", Service: "edgar-mna", PlanID: "abc123",
		Actor: "conan", Target: "svc-deploy@ec2-alerts-prod",
		FromRef: "v0.1.0", ToRef: "v0.2.0",
		Outcome: "success", Duration: "31s",
		Detail: map[string]any{"action": "update"},
	}
	if err := log.Append(in); err != nil {
		t.Fatal(err)
	}

	entries, err := log.History("edgar-mna")
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("want 1 entry, got %d", len(entries))
	}
	got := entries[0]
	if got.Service != in.Service || got.ToRef != in.ToRef || got.Outcome != in.Outcome {
		t.Errorf("entry did not round-trip: %+v", got)
	}
	if got.Timestamp.IsZero() {
		t.Error("Append must stamp entries that arrive without a timestamp")
	}
}

func TestAppendNeverRewritesExistingEntries(t *testing.T) {
	log := tempLog(t)
	if err := log.Append(Entry{Event: "apply", Service: "a", Outcome: "success"}); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(log.Path)

	if err := log.Append(Entry{Event: "apply", Service: "b", Outcome: "failed"}); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(log.Path)

	if !strings.HasPrefix(string(after), string(before)) {
		t.Fatal("a second Append altered bytes that were already on disk; the log must be append-only")
	}
	if lines := strings.Count(string(after), "\n"); lines != 2 {
		t.Errorf("want 2 newline-terminated entries, got %d", lines)
	}
}

// A crash mid-write leaves a truncated trailing line. That must cost exactly
// one entry, not the whole history — the log is read during incidents, which
// is precisely when a previous run may have died uncleanly.
func TestHistorySurvivesATruncatedTrailingLine(t *testing.T) {
	log := tempLog(t)
	for _, svc := range []string{"a", "b"} {
		if err := log.Append(Entry{Event: "apply", Service: svc, Outcome: "success"}); err != nil {
			t.Fatal(err)
		}
	}
	f, err := os.OpenFile(log.Path, os.O_APPEND|os.O_WRONLY, 0o640)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(`{"ts":"2026-01-01T00:00:00Z","event":"app`); err != nil {
		t.Fatal(err)
	}
	f.Close()

	entries, err := log.History("")
	if err != nil {
		t.Fatalf("History must not fail on a corrupt line: %v", err)
	}
	if len(entries) != 2 {
		t.Fatalf("corrupt trailing line should cost 1 entry, not the history; got %d of 2", len(entries))
	}
}

func TestHistorySkipsCorruptLinesInTheMiddle(t *testing.T) {
	log := tempLog(t)
	if err := log.Append(Entry{Event: "apply", Service: "a"}); err != nil {
		t.Fatal(err)
	}
	f, _ := os.OpenFile(log.Path, os.O_APPEND|os.O_WRONLY, 0o640)
	f.WriteString("not json at all\n")
	f.Close()
	if err := log.Append(Entry{Event: "apply", Service: "b"}); err != nil {
		t.Fatal(err)
	}

	entries, err := log.History("")
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatalf("entries after a corrupt line must still be readable; got %d of 2", len(entries))
	}
	if entries[1].Service != "b" {
		t.Errorf("history order lost: %+v", entries)
	}
}

func TestHistoryOnAMissingFileIsEmptyNotAnError(t *testing.T) {
	log, err := Open(filepath.Join(t.TempDir(), "never-written.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	entries, err := log.History("")
	if err != nil {
		t.Fatalf("a fleet with no deploys yet is not an error: %v", err)
	}
	if len(entries) != 0 {
		t.Errorf("want empty history, got %d entries", len(entries))
	}
}

func TestHistoryFiltersByService(t *testing.T) {
	log := tempLog(t)
	for _, svc := range []string{"edgar-mna", "fda-catalysts", "edgar-mna"} {
		if err := log.Append(Entry{Event: "apply", Service: svc}); err != nil {
			t.Fatal(err)
		}
	}
	entries, _ := log.History("edgar-mna")
	if len(entries) != 2 {
		t.Errorf("want 2 edgar-mna entries, got %d", len(entries))
	}
}

// Rollback targets the last ref that actually worked. A failed deploy of
// v0.3.0 must not become the rollback target, and neither may the ref we are
// rolling away from.
func TestLastSuccessfulRefSkipsFailuresAndTheCurrentRef(t *testing.T) {
	log := tempLog(t)
	seq := []Entry{
		{Event: "apply", Service: "s", ToRef: "v0.1.0", Outcome: "success"},
		{Event: "apply", Service: "s", ToRef: "v0.2.0", Outcome: "success"},
		{Event: "apply", Service: "s", ToRef: "v0.3.0", Outcome: "failed"},
		{Event: "rollback", Service: "s", ToRef: "v0.2.0", Outcome: "success"},
	}
	for i, e := range seq {
		e.Timestamp = time.Date(2026, 1, 1, 0, i, 0, 0, time.UTC)
		if err := log.Append(e); err != nil {
			t.Fatal(err)
		}
	}

	ref, err := log.LastSuccessfulRef("s", "v0.2.0")
	if err != nil {
		t.Fatal(err)
	}
	if ref != "v0.1.0" {
		t.Errorf("want v0.1.0 (last success that is not the current ref), got %s", ref)
	}
}

func TestLastSuccessfulRefFailsCleanlyWhenNothingQualifies(t *testing.T) {
	log := tempLog(t)
	if err := log.Append(Entry{Event: "apply", Service: "s", ToRef: "v0.1.0", Outcome: "failed"}); err != nil {
		t.Fatal(err)
	}
	if _, err := log.LastSuccessfulRef("s", ""); err == nil {
		t.Fatal("with no successful deploy on record there is nothing safe to roll back to; this must be an error")
	}
}
