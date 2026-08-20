package engine

import (
	"fmt"
	"strings"

	"github.com/conanohara/alert-platform/internal/exec"
	"github.com/conanohara/alert-platform/internal/fleet"
)

// Resolver turns the secret *names* in a spec into values at deploy time.
//
// Specs reference secrets by name and never carry values (docs/spec.md, D3).
// This is where the indirection is finally cashed in — and deliberately the
// only place, so there is exactly one code path that touches credentials.
type Resolver interface {
	Resolve(names []string) (map[string]string, error)
	Describe() string
}

// SSMResolver reads from AWS SSM Parameter Store under the fleet's prefix.
//
// It shells out to the AWS CLI *on the target host* rather than using the
// SDK from the operator's machine. Two reasons: the values never transit the
// operator's terminal or shell history, and the host already needs an
// instance role for its own runtime — so the deploy uses the same identity
// that the audit trail attributes it to.
type SSMResolver struct {
	Runner exec.Runner
	Prefix string
}

func NewSSMResolver(r exec.Runner, prefix string) *SSMResolver {
	return &SSMResolver{Runner: r, Prefix: strings.TrimRight(prefix, "/")}
}

func (s *SSMResolver) Describe() string { return "aws_ssm:" + s.Prefix }

func (s *SSMResolver) Resolve(names []string) (map[string]string, error) {
	out := make(map[string]string, len(names))
	if len(names) == 0 {
		return out, nil
	}

	// One call per secret rather than get-parameters-by-path: a typo'd name
	// then fails loudly on that name instead of silently yielding a short
	// map that only shows up as a crash loop after the restart.
	for _, name := range names {
		path := s.Prefix + "/" + name
		cmd := fmt.Sprintf(
			"aws ssm get-parameter --name %q --with-decryption --query Parameter.Value --output text",
			path)
		res, err := s.Runner.Run(cmd)
		if err != nil {
			return nil, fmt.Errorf("resolve %s: %w", path, err)
		}
		if !res.OK() {
			return nil, fmt.Errorf(
				"secret %q not readable at %s (exit %d). Check the parameter exists and the "+
					"host's instance role grants ssm:GetParameter on it.\n%s",
				name, path, res.ExitCode, strings.TrimSpace(res.Stderr))
		}
		value := strings.TrimRight(res.Stdout, "\r\n")
		if value == "" {
			return nil, fmt.Errorf("secret %q at %s resolved to an empty value", name, path)
		}
		out[name] = value
	}
	return out, nil
}

// StubResolver returns fixed values. Used by --dry-run and the tests so the
// full apply path can be exercised without touching a real secret store.
type StubResolver struct{ Value string }

func (s StubResolver) Describe() string { return "stub" }

func (s StubResolver) Resolve(names []string) (map[string]string, error) {
	out := make(map[string]string, len(names))
	for _, n := range names {
		v := s.Value
		if v == "" {
			v = SecretPlaceholder
		}
		out[n] = v
	}
	return out, nil
}

// ResolverFor builds the resolver named by the fleet's delivery defaults.
func ResolverFor(f fleet.Fleet, r exec.Runner, dryRun bool) (Resolver, error) {
	eff := fleet.Effective{Config: f.Defaults}
	backend := eff.String("delivery.secrets_backend", "aws_ssm")
	prefix := eff.String("delivery.secrets_prefix", "")

	if dryRun {
		return StubResolver{}, nil
	}
	switch backend {
	case "aws_ssm":
		if prefix == "" {
			return nil, fmt.Errorf("defaults.delivery.secrets_prefix is required for aws_ssm")
		}
		return NewSSMResolver(r, prefix), nil
	default:
		return nil, fmt.Errorf("unsupported secrets_backend %q", backend)
	}
}
