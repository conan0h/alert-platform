package exec

import (
	"errors"
	"testing"
)

// ssh reserves exit 255 for its own failures rather than the remote
// command's exit code. If that leaks through as a successful result, an
// unreachable host reads as an empty one and plan proposes creating a
// fleet that is already running.
func TestCheckSSHResult(t *testing.T) {
	boom := errors.New("dial failed")

	cases := []struct {
		name    string
		res     Result
		inErr   error
		wantErr bool
	}{
		{"ssh transport failure", Result{ExitCode: 255, Stderr: "connection refused"}, nil, true},
		{"remote command succeeded", Result{ExitCode: 0}, nil, false},
		{"remote command failed", Result{ExitCode: 1, Stderr: "no such unit"}, nil, false},
		{"existing error propagates", Result{ExitCode: 255}, boom, true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res, err := checkSSHResult(tc.res, tc.inErr, "svc-deploy@host")
			if (err != nil) != tc.wantErr {
				t.Fatalf("wantErr=%v, got err=%v", tc.wantErr, err)
			}
			if res.ExitCode != tc.res.ExitCode {
				t.Fatalf("result mutated: got %d, want %d", res.ExitCode, tc.res.ExitCode)
			}
			if tc.inErr != nil && !errors.Is(err, tc.inErr) {
				t.Fatalf("original error not propagated: got %v", err)
			}
		})
	}
}
