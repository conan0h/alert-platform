// Package buildinfo reports which commit produced the running alertctl.
//
// The host builds alertctl from its own checkout, and only `plan` moves that
// checkout, so a read verb can answer from a binary built several commits
// ago. Nothing said so, and a stale answer is indistinguishable from a
// current one: on 2026-09-22 a `drift` exit 0 was read as "production matches
// main" when it meant "production matches the specs the host last synced".
//
// The Go toolchain already records the revision in the binary, so reporting
// it needs no change to how the host builds — which matters, because the
// build command lives in the wrapper and the wrapper is not the agent's to
// change. That stamping is conditional (it needs a git checkout the builder
// can read), so an empty Stamp is a normal outcome and says so rather than
// being suppressed.
package buildinfo

import (
	"fmt"
	"runtime/debug"
	"strings"
)

// shortLen matches the 12 hex digits the Go module system uses in a
// pseudo-version, so a revision printed here can be pasted into a `git show`
// and compared against a module version without re-truncating either.
const shortLen = 12

// Stamp is what the binary knows about its own origin. The zero value means
// the toolchain recorded nothing, which happens when the build ran outside a
// readable git checkout.
type Stamp struct {
	Revision string `json:"revision,omitempty"`
	BuiltAt  string `json:"built_at,omitempty"`
	Modified bool   `json:"modified,omitempty"`
}

// Read returns the stamp the toolchain wrote into this binary.
func Read() Stamp { return stampFrom(debug.ReadBuildInfo()) }

// stampFrom is Read with its source injected, so the tests can drive every
// combination the toolchain can produce without building a binary per case.
func stampFrom(info *debug.BuildInfo, ok bool) Stamp {
	var s Stamp
	if !ok || info == nil {
		return s
	}
	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs.revision":
			s.Revision = setting.Value
		case "vcs.time":
			s.BuiltAt = setting.Value
		case "vcs.modified":
			s.Modified = setting.Value == "true"
		}
	}
	// A dirty flag with no revision describes nothing, and a build time with
	// no revision cannot be compared against anything. Either way there is no
	// commit to report, so report none rather than half of one.
	if s.Revision == "" {
		return Stamp{}
	}
	return s
}

// Known reports whether the toolchain stamped this binary at all.
func (s Stamp) Known() bool { return s.Revision != "" }

// Short is the revision truncated for reading, or "" when there is none.
func (s Stamp) Short() string {
	if len(s.Revision) <= shortLen {
		return s.Revision
	}
	return s.Revision[:shortLen]
}

// Matches reports whether this binary was built from the given commit. It
// takes either a full or an abbreviated sha, because the caller comparing
// against it is usually a workflow holding github.sha and a human holding
// seven characters.
func (s Stamp) Matches(rev string) bool {
	if !s.Known() || rev == "" {
		return false
	}
	a, b := strings.ToLower(s.Revision), strings.ToLower(rev)
	if len(a) > len(b) {
		a, b = b, a
	}
	return strings.HasPrefix(b, a)
}

// Line is the one-line report every read verb prints before its answer. The
// prefix is fixed so `ssm-run` can find it in the host's stdout without
// parsing the verb's own output format.
func (s Stamp) Line() string {
	return "control plane: alertctl " + s.describe()
}

func (s Stamp) describe() string {
	if !s.Known() {
		return "(unstamped build — no revision recorded; built outside a readable git checkout)"
	}
	out := s.Short()
	if s.BuiltAt != "" {
		out += fmt.Sprintf(" built %s", s.BuiltAt)
	}
	if s.Modified {
		// Uncommitted changes mean the revision names a commit the binary is
		// not, which is worse than no revision at all if it goes unsaid.
		out += " (modified working tree)"
	}
	return out
}
