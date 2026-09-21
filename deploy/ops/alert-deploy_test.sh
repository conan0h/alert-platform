#!/usr/bin/env bash
#
# Tests for the argument validation in alert-deploy.
#
# This is the file worth reading twice. alert-deploy is the whole blast
# radius of the deploy pipeline: IAM says which instance and which documents,
# and everything past that point is decided here. So these cases are mostly
# about what the wrapper *refuses* — a verb that is not in the set, an
# argument that is not the shape it claims to be, an attempt to reach a shell
# through one of them.
#
# ALERT_DEPLOY_DRYRUN makes the wrapper print the command it would run instead
# of running it. Validation happens first and is unaffected, so a case that
# expects a refusal is testing the real refusal.
#
#   ./deploy/ops/alert-deploy_test.sh

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WRAPPER="$HERE/alert-deploy"

pass=0; fail=0

# expect <expected-exit> <description> -- <args...>
expect() {
  local want=$1 desc=$2; shift 3
  local out rc
  out=$(ALERT_DEPLOY_DRYRUN=1 "$WRAPPER" "$@" 2>&1); rc=$?
  if [[ $rc -eq $want ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf 'FAIL: %s\n  args: %s\n  want exit %d, got %d\n  output: %s\n' \
      "$desc" "$*" "$want" "$rc" "$out" >&2
  fi
}

# expect_output <substring> <description> -- <args...>
expect_output() {
  local want=$1 desc=$2; shift 3
  local out
  out=$(ALERT_DEPLOY_DRYRUN=1 "$WRAPPER" "$@" 2>&1)
  if [[ $out == *"$want"* ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf 'FAIL: %s\n  args: %s\n  want output containing: %s\n  got: %s\n' \
      "$desc" "$*" "$want" "$out" >&2
  fi
}

# -- the verb set is closed -------------------------------------------------

expect 2 "no verb at all prints usage" -- 
for verb in status drift health; do
  expect 0 "read-only verb '$verb' is accepted" -- "$verb"
done
expect 0 "plan is accepted" -- plan
expect 0 "apply with a well-formed plan id is accepted" -- apply 0123456789ab

# help is a request, not a misuse, so it succeeds — which is also what lets
# bootstrap-host.sh tell "sudo ran it" apart from "it rejected the arguments".
for h in help -h --help; do
  expect 0 "'$h' is answered rather than refused" -- "$h"
done

# Anything outside the set, including alertctl subcommands the wrapper
# deliberately does not expose. `rollback` rewrites a spec and belongs in a
# reviewed PR; `serve` would put a listener on the host.
for verb in rollback serve render validate bash sh exec ''; do
  expect 2 "verb '$verb' is refused" -- "$verb"
done

# -- no break-glass reachable from here ------------------------------------

expect 2 "--skip-gates is not an argument this wrapper knows" -- apply 0123456789ab --skip-gates
expect 2 "--no-rollback cannot be requested remotely" -- apply 0123456789ab --no-rollback
expect 2 "a second positional cannot smuggle a flag in" -- plan --target ssh

# -- plan ids ---------------------------------------------------------------

expect 2 "apply with no plan id is refused" -- apply
expect 2 "a non-hex plan id is refused" -- apply zzzzzzzzzzzz
expect 2 "a short plan id is refused" -- apply 0123456789
expect 2 "a long plan id is refused" -- apply 0123456789abc
expect 2 "a path traversal in place of a plan id is refused" -- apply ../../etc/passwd
expect 2 "a plan id with a shell metacharacter is refused" -- apply '0123456789ab;id'

# -- actor ------------------------------------------------------------------

expect 0 "the actor the deploy workflow sets is accepted" -- plan --actor gha:1234567890
expect 2 "an actor with a space is refused" -- plan --actor 'a b'
# shellcheck disable=SC2016  # the literal $(id) is the payload; expanding it would test nothing
expect 2 "an actor with a shell metacharacter is refused" -- plan --actor 'x$(id)'
expect 2 "an actor with a newline is refused" -- plan --actor $'a\nb'
expect 2 "--actor with no value is refused" -- plan --actor
expect 2 "a 65-character actor is refused" -- plan --actor "$(printf 'a%.0s' {1..65})"
expect 0 "a 64-character actor is accepted" -- plan --actor "$(printf 'a%.0s' {1..64})"

# -- logs / history numeric and time arguments ------------------------------

expect 0 "a relative window is accepted" -- logs --since 2h
expect 0 "a quoted 'N units ago' window is accepted" -- logs --since '30 minutes ago'
expect 0 "a plain date is accepted" -- logs --since 2026-09-21
expect 2 "a journalctl option cannot be smuggled through --since" -- logs --since '--output=cat'
# shellcheck disable=SC2016  # ditto: this must reach the wrapper unexpanded
expect 2 "a command substitution in --since is refused" -- logs --since '$(reboot)'
expect 0 "a line count is accepted" -- history --lines 50
expect 2 "a non-numeric line count is refused" -- history --lines abc
expect 2 "a negative line count is refused" -- history --lines -5

# -- every verb can actually run --------------------------------------------
#
# The gap that produced the bug these cover: alertctl is a derived file, built
# from the checkout rather than shipped, and the build lived inside `plan`
# alone. On a host that had been bootstrapped but never planned, every
# read-only verb died with "No such file or directory" — found by running
# `observe` against the real host, not here, because the cases above only
# exercise argument parsing and never asked whether the command could run.

for verb in status drift history; do
  expect_output "go build -C" "read-only verb '$verb' makes sure the binary exists first" -- "$verb"
done
expect_output "go build -C" "apply makes sure the binary exists first" -- apply 0123456789ab
expect_output "go build -C" "plan builds the binary" -- plan

# Only plan may move the checkout. If a read-only verb synced first, `status`
# would report against a tree nobody asked it to fetch, and the plan `apply`
# validates would no longer describe what is on disk.
for verb in status drift history; do
  out=$(ALERT_DEPLOY_DRYRUN=1 "$WRAPPER" "$verb" 2>&1)
  if [[ $out == *"reset --hard"* || $out == *"fetch"* ]]; then
    fail=$((fail + 1))
    printf 'FAIL: read-only verb %s moved the checkout\n  output: %s\n' "$verb" "$out" >&2
  else
    pass=$((pass + 1))
  fi
done

# -- the build actually works from somewhere else ---------------------------
#
# Every dry-run case above passed while the build was broken, because they
# print the command and never run it. The bug: `go build -o out /abs/path/pkg`
# resolves the package against the module owning the *current directory*, so
# it fails with "go.mod file not found" for any caller outside the checkout —
# which is every real caller, since the wrapper runs under sudo from an
# arbitrary cwd and bootstrap runs from /tmp.
#
# So this one compiles for real, from a directory that is not the checkout.
REPO=$(cd "$HERE/../.." && pwd)
if [[ ! -f $REPO/go.mod ]]; then
  printf 'FAIL: expected a go.mod at %s\n' "$REPO" >&2
  fail=$((fail + 1))
elif ! command -v go >/dev/null 2>&1; then
  # Loud, not silent. A skipped check that looks like a pass is how the
  # ref-coupling bug survived in internal/engine; CI installs Go for this job
  # so this branch means a local run, not a green build.
  printf 'NOTE: go not installed — the build check did not run\n' >&2
else
  build_tmp=$(mktemp -d)
  if ( cd "$build_tmp" && env GOFLAGS=-mod=vendor go build -C "$REPO" -o "$build_tmp/alertctl" ./cmd/alertctl ) 2>"$build_tmp/err"; then
    if [[ -x $build_tmp/alertctl ]]; then
      pass=$((pass + 1))
    else
      fail=$((fail + 1))
      printf 'FAIL: build reported success but produced no binary\n' >&2
    fi
  else
    fail=$((fail + 1))
    printf 'FAIL: alertctl does not build from a cwd outside the checkout\n  %s\n' \
      "$(cat "$build_tmp/err")" >&2
  fi
  rm -rf "$build_tmp"
fi

# -- the commands it actually builds ---------------------------------------

expect_output "-target local" "alertctl is driven against the local host, not over ssh" -- status
expect_output "-auto-approve" "apply does not wait for a prompt nobody can answer" -- apply 0123456789ab
expect_output "reset --quiet --hard origin/main" "plan moves the checkout to origin/main and nowhere else" -- plan

# apply must not move the checkout: what is applied is what was planned.
out=$(ALERT_DEPLOY_DRYRUN=1 "$WRAPPER" apply 0123456789ab 2>&1)
if [[ $out == *"git"* ]]; then
  fail=$((fail + 1))
  printf 'FAIL: apply touched git; the plan it validates would no longer describe the tree\n  output: %s\n' "$out" >&2
else
  pass=$((pass + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
