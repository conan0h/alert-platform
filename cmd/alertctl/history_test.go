package main

import (
	"testing"

	"github.com/conan0h/alert-platform/internal/audit"
)

func TestFailureScope(t *testing.T) {
	for _, tc := range []struct {
		name    string
		outcome string
		detail  map[string]any
		want    string
	}{
		{"refused before acting", "failed", map[string]any{"mutated": false}, "  host untouched"},
		{"failed part-way", "failed", map[string]any{"mutated": true}, "  host changed"},
		{"written before the field existed", "failed", map[string]any{"action": "update"}, ""},
		{"success says nothing", "success", map[string]any{"mutated": true}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := failureScope(audit.Entry{Outcome: tc.outcome, Detail: tc.detail})
			if got != tc.want {
				t.Errorf("failureScope = %q, want %q", got, tc.want)
			}
		})
	}
}
