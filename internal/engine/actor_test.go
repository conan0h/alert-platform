package engine

import "testing"

// Who a deploy is attributed to is the audit log's whole point, and the
// automated path is the one that can get it wrong silently: on the host the
// wrapper runs as the shared `alert-ops` account under sudo, so without
// ALERTCTL_ACTOR every workflow deploy would be filed under one service
// account with no way back to the run that caused it.
func TestCurrentUserPrefersTheExplicitActor(t *testing.T) {
	for _, tc := range []struct {
		name string
		env  map[string]string
		want string
	}{
		{
			name: "explicit actor beats the shell's idea of the user",
			env:  map[string]string{"ALERTCTL_ACTOR": "gha:1234567890", "SUDO_USER": "alert-ops", "USER": "root"},
			want: "gha:1234567890",
		},
		{
			name: "sudo user still wins for a human at a terminal",
			env:  map[string]string{"SUDO_USER": "conan", "USER": "root"},
			want: "conan",
		},
		{
			name: "an empty actor does not shadow the fallbacks",
			env:  map[string]string{"ALERTCTL_ACTOR": "", "SUDO_USER": "conan"},
			want: "conan",
		},
		{
			name: "a whitespace-only actor is not an attribution",
			env:  map[string]string{"ALERTCTL_ACTOR": "   ", "SUDO_USER": "conan"},
			want: "conan",
		},
		{
			name: "nothing set at all is recorded as unknown, never blank",
			env:  map[string]string{},
			want: "unknown",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, key := range []string{"ALERTCTL_ACTOR", "SUDO_USER", "USER", "LOGNAME"} {
				t.Setenv(key, "")
			}
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			if got := currentUser(); got != tc.want {
				t.Errorf("currentUser() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestSanitizeActorKeepsAuditEntriesReadable(t *testing.T) {
	for _, tc := range []struct{ in, want, why string }{
		{"gha:1234567890", "gha:1234567890", "the value the deploy workflow sets must survive untouched"},
		{"conan", "conan", "an ordinary username is unchanged"},
		{"svc-deploy@ec2-alerts-prod", "svc-deploy@ec2-alerts-prod", "host-qualified names are unchanged"},
		{"  conan  ", "conan", "surrounding whitespace is trimmed"},
		{"a\nb", "a_b", "a newline becomes an underscore rather than vanishing"},
		{`x"; DROP`, "x___DROP", "quotes and punctuation are replaced, not dropped"},
		{"", "", "empty stays empty so the caller falls through"},
	} {
		if got := sanitizeActor(tc.in); got != tc.want {
			t.Errorf("sanitizeActor(%q) = %q, want %q — %s", tc.in, got, tc.want, tc.why)
		}
	}

	// Bounded, because it is appended to every entry of a log an operator
	// reads during an incident.
	long := ""
	for i := 0; i < 200; i++ {
		long += "a"
	}
	if got := sanitizeActor(long); len(got) != 64 {
		t.Errorf("a 200-char actor was not truncated to 64: got %d", len(got))
	}
}
