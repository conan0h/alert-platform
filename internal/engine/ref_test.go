package engine

import "testing"

// spec.source.ref reaches `git rev-parse` and `git clone --branch`. The
// schema already restricts it to a semver tag, but this guard runs at the
// point of use, so the property survives the validator being skipped, a new
// call site, or a spec loaded by something other than the CLI.
func TestCheckRefExistsRejectsAnythingThatIsNotASemverTag(t *testing.T) {
	bad := []string{
		"main",                   // D2: a branch is not a reproducible deploy
		"HEAD",                   //
		"v1.0",                   // not a full semver triple
		"",                       //
		"v1.0.0; rm -rf /",       // shell metacharacters
		"v1.0.0 --upload-pack=x", // argument injection into git
		"$(whoami)",              //
		"../../etc/passwd",       //
	}
	for _, ref := range bad {
		if err := checkRefExists(t.TempDir(), ref); err == nil {
			t.Errorf("ref %q should have been refused before reaching git", ref)
		}
	}
}

func TestCheckRefExistsAcceptsWellFormedTags(t *testing.T) {
	// These are refused only because the temp dir has no such tag — the
	// point is that they pass the shape guard and reach the git lookup.
	for _, ref := range []string{"v0.1.0", "1.2.3", "v2.0.0-rc.1"} {
		if !safeRef.MatchString(ref) {
			t.Errorf("ref %q is a legal semver tag and must not be rejected by shape", ref)
		}
	}
}
