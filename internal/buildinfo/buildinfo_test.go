package buildinfo

import (
	"runtime/debug"
	"strings"
	"testing"
)

func info(settings ...debug.BuildSetting) *debug.BuildInfo {
	return &debug.BuildInfo{Settings: settings}
}

func set(k, v string) debug.BuildSetting { return debug.BuildSetting{Key: k, Value: v} }

const rev = "6c6674e38ec8222b8c5497cbe79f3e96b93917c0"

func TestStampFrom(t *testing.T) {
	cases := []struct {
		name string
		in   *debug.BuildInfo
		ok   bool
		want Stamp
	}{
		{
			name: "a normal build carries revision, time and a clean tree",
			in: info(set("vcs", "git"), set("vcs.revision", rev),
				set("vcs.time", "2026-09-24T08:35:23Z"), set("vcs.modified", "false")),
			ok:   true,
			want: Stamp{Revision: rev, CommittedAt: "2026-09-24T08:35:23Z"},
		},
		{
			name: "a dirty tree is carried through",
			in: info(set("vcs.revision", rev), set("vcs.time", "2026-09-24T08:35:23Z"),
				set("vcs.modified", "true")),
			ok:   true,
			want: Stamp{Revision: rev, CommittedAt: "2026-09-24T08:35:23Z", Modified: true},
		},
		{
			name: "no VCS settings at all is the unstamped build",
			in:   info(set("GOARCH", "amd64"), set("-mod", "vendor")),
			ok:   true,
			want: Stamp{},
		},
		{
			// Whatever settings survive, without a revision there is nothing
			// to compare a host's answer against, so the stamp is empty.
			name: "a commit time without a revision reports nothing",
			in:   info(set("vcs.time", "2026-09-24T08:35:23Z"), set("vcs.modified", "true")),
			ok:   true,
			want: Stamp{},
		},
		{
			name: "no build info at all",
			in:   nil,
			ok:   false,
			want: Stamp{},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := stampFrom(tc.in, tc.ok)
			if got != tc.want {
				t.Fatalf("stampFrom() = %+v, want %+v", got, tc.want)
			}
		})
	}
}

func TestLine(t *testing.T) {
	cases := []struct {
		name  string
		stamp Stamp
		want  string
	}{
		{
			name:  "clean build",
			stamp: Stamp{Revision: rev, CommittedAt: "2026-09-24T08:35:23Z"},
			want:  "control plane: alertctl 6c6674e38ec8 (committed 2026-09-24T08:35:23Z)",
		},
		{
			name:  "dirty build says so, because the revision is then a lie by itself",
			stamp: Stamp{Revision: rev, CommittedAt: "2026-09-24T08:35:23Z", Modified: true},
			want:  "control plane: alertctl 6c6674e38ec8 (committed 2026-09-24T08:35:23Z, modified working tree)",
		},
		{
			name:  "revision with no commit time",
			stamp: Stamp{Revision: rev},
			want:  "control plane: alertctl 6c6674e38ec8",
		},
		{
			name:  "unstamped",
			stamp: Stamp{},
			want:  "control plane: alertctl (unstamped build — no revision recorded; built outside a readable git checkout)",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.stamp.Line(); got != tc.want {
				t.Fatalf("Line() = %q, want %q", got, tc.want)
			}
		})
	}
}

// The prefix is a contract: ssm-run greps the host's stdout for it. A change
// here without a matching change in stamp.sh silently stops the report.
func TestLinePrefixIsStable(t *testing.T) {
	for _, s := range []Stamp{{}, {Revision: rev}} {
		if !strings.HasPrefix(s.Line(), "control plane: alertctl ") {
			t.Fatalf("Line() = %q, want the fixed prefix ssm-run looks for", s.Line())
		}
	}
}

func TestMatches(t *testing.T) {
	stamp := Stamp{Revision: rev}
	cases := []struct {
		name string
		in   string
		want bool
	}{
		{"the full sha a workflow holds", rev, true},
		{"the twelve characters we print", rev[:12], true},
		{"the seven a human pastes", rev[:7], true},
		{"case does not matter", strings.ToUpper(rev), true},
		{"a different commit", "0f0541b43220d1c06e570ac0d849a39d1166499b", false},
		{"empty is never a match", "", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := stamp.Matches(tc.in); got != tc.want {
				t.Fatalf("Matches(%q) = %v, want %v", tc.in, got, tc.want)
			}
		})
	}

	if (Stamp{}).Matches(rev) {
		t.Fatal("an unstamped binary must not claim to match a commit")
	}
}

func TestShort(t *testing.T) {
	if got := (Stamp{Revision: rev}).Short(); got != "6c6674e38ec8" {
		t.Fatalf("Short() = %q", got)
	}
	// A short revision is returned whole rather than padded or panicking.
	if got := (Stamp{Revision: "abc123"}).Short(); got != "abc123" {
		t.Fatalf("Short() = %q", got)
	}
	if got := (Stamp{}).Short(); got != "" {
		t.Fatalf("Short() = %q, want empty", got)
	}
}

// Read runs against whatever built the test binary. It must not panic and
// must be self-consistent, which is all that can be asserted without
// controlling the build.
func TestReadIsSelfConsistent(t *testing.T) {
	s := Read()
	if s.Known() != (s.Revision != "") {
		t.Fatalf("Known() disagrees with Revision: %+v", s)
	}
	if !s.Known() && (s.CommittedAt != "" || s.Modified) {
		t.Fatalf("an unknown stamp must be empty: %+v", s)
	}
}
