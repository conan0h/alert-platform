#!/usr/bin/env bash
#
# Tests for verdict.sh — which host exits fail a run, and which only annotate.
#
# The case that matters is the middle one: `drift` exiting 3 must pass, and a
# `drift` that could not run at all (exit 1) must fail, even though SSM reports
# both identically as Failed. Getting that backwards either makes the hourly
# observe job permanently red or silently swallows an unreachable host.
#
#   ./.github/actions/ssm-run/verdict_test.sh

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VERDICT="$HERE/verdict.sh"

pass=0; fail=0

# check <want-exit> <want-output-substring|-> <desc> -- <args...>
check() {
  local want=$1 want_out=$2 desc=$3; shift 4
  local out rc
  out=$(GITHUB_OUTPUT=/dev/null "$VERDICT" "$@" 2>&1); rc=$?
  if [[ $rc -ne $want ]]; then
    fail=$((fail + 1))
    printf 'FAIL: %s\n  args: %s\n  want exit %d, got %d\n  output: %s\n' "$desc" "$*" "$want" "$rc" "$out" >&2
    return
  fi
  if [[ $want_out != "-" && $out != *"$want_out"* ]]; then
    fail=$((fail + 1))
    printf 'FAIL: %s\n  args: %s\n  want output containing %q\n  got: %s\n' "$desc" "$*" "$want_out" "$out" >&2
    return
  fi
  pass=$((pass + 1))
}

# -- success is success, whatever else is passed ----------------------------

check 0 - "Success passes"                        -- Success 0 '' doc i-123
check 0 - "Success passes even with a finding set" -- Success 0 '3' doc i-123

# -- the distinction this file exists for ----------------------------------

check 0 '::warning' "a declared finding annotates and passes" \
  -- Failed 3 '3' AlertPlatform-Observe i-123
check 1 '::error' "a failure with the same verb still fails" \
  -- Failed 1 '3' AlertPlatform-Observe i-123
check 1 '::error' "a usage error is not a finding" \
  -- Failed 2 '3' AlertPlatform-Observe i-123

# The write path declares no findings, so nothing is excused there.
check 1 '::error' "no finding set means every non-zero exit fails" \
  -- Failed 3 '' AlertPlatform-Deploy i-123

# -- multiple findings, and near-misses ------------------------------------

check 0 '::warning' "a finding set may list several codes" -- Failed 4 '3 4 5' doc i-123
check 1 '::error'   "a code outside the set fails"          -- Failed 6 '3 4 5' doc i-123
# 3 must not match because it is a prefix or substring of 30 or 13.
check 1 '::error'   "codes are compared exactly, not as substrings" -- Failed 30 '3' doc i-123
check 1 '::error'   "a code is not matched by suffix"               -- Failed 13 '3' doc i-123

# -- states that are not Success and not an exit code ----------------------
#
# TimedOut and Cancelled arrive with no ResponseCode at all. Those are
# operational failures and must never be excused by a finding set.
check 1 '::error' "TimedOut with no exit code fails"  -- TimedOut '' '3' doc i-123
check 1 '::error' "Cancelled with no exit code fails" -- Cancelled '' '3' doc i-123
check 1 'host exit unknown' "a missing exit code is reported as unknown" -- TimedOut '' '' doc i-123

# -- a malformed finding set is a misconfiguration, not a licence ----------

check 1 'space-separated numbers' "a non-numeric finding set fails"   -- Failed 3 'three' doc i-123
check 1 'space-separated numbers' "a finding set cannot smuggle code" -- Failed 3 '3; rm -rf /' doc i-123

# -- the output the action reads -------------------------------------------

out_file=$(mktemp)
GITHUB_OUTPUT="$out_file" "$VERDICT" Failed 3 '3' doc i-123 >/dev/null 2>&1
if [[ $(cat "$out_file") == *"finding=true"* ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  printf 'FAIL: a finding does not set the finding output\n  got: %s\n' "$(cat "$out_file")" >&2
fi
rm -f "$out_file"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
