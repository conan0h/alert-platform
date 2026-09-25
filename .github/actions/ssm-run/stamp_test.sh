#!/usr/bin/env bash
#
# Tests for stamp.sh — which control plane answered, and whether it is current.
#
# The cases that matter: a matching revision must not annotate (an hourly
# notice that says nothing is noise), a differing one must, and a verb that
# never runs alertctl must produce no output at all.
#
#   ./.github/actions/ssm-run/stamp_test.sh

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STAMP="$HERE/stamp.sh"

REV=6c6674e38ec8222b8c5497cbe79f3e96b93917c0
OTHER=19ae3e2f1c4b8a9d0e5f6a7b8c9d0e1f2a3b4c5d

pass=0; fail=0

# check <want-substring|-> <must-not-contain|-> <desc> <expected-sha> <<< stdin
check() {
  local want=$1 forbid=$2 desc=$3 expected=$4 input=$5
  local out rc
  out=$(GITHUB_STEP_SUMMARY=/dev/null "$STAMP" "$expected" <<<"$input" 2>&1); rc=$?
  if [[ $rc -ne 0 ]]; then
    fail=$((fail + 1))
    printf 'FAIL: %s\n  stamp.sh exited %d (it must always exit 0)\n  output: %s\n' "$desc" "$rc" "$out" >&2
    return
  fi
  if [[ $want != "-" && $out != *"$want"* ]]; then
    fail=$((fail + 1))
    printf 'FAIL: %s\n  want output containing %q\n  got: %s\n' "$desc" "$want" "$out" >&2
    return
  fi
  if [[ $forbid != "-" && $out == *"$forbid"* ]]; then
    fail=$((fail + 1))
    printf 'FAIL: %s\n  output must not contain %q\n  got: %s\n' "$desc" "$forbid" "$out" >&2
    return
  fi
  pass=$((pass + 1))
}

status_output() {
  printf 'control plane: alertctl %s\nSERVICE          REF\nedgar-mna        v0.4.0\n' "$1"
}

# -- the current case: report it, annotate nothing --------------------------

check "${REV:0:12}" '::notice' "a matching revision is reported without a notice" \
  "$REV" "$(status_output "${REV:0:12} built 2026-09-24T08:35:23Z")"

# The host prints twelve characters and the workflow holds forty, so the
# comparison has to be on the shorter of the two or every run looks stale.
check 'dispatched from' - "the printed abbreviation matches the full sha" \
  "$REV" "$(status_output "${REV:0:12}")"

# -- the case this exists for ----------------------------------------------

check '::notice' - "a stale control plane is annotated" \
  "$OTHER" "$(status_output "${REV:0:12} built 2026-09-24T08:35:23Z")"
check "$OTHER" - "the annotation names the commit the run expected" \
  "$OTHER" "$(status_output "${REV:0:12}")"

# -- silence where there is nothing to say ---------------------------------

check '-' 'Control plane' "logs output produces no report at all" \
  "$REV" "$(printf 'Sep 25 08:00:01 host alert-edgar-mna[1]: poll cycle complete\n')"
check '-' '::notice' "health output produces no notice" \
  "$REV" "$(printf 'edgar-mna            unit=active     healthz=ok\n')"

# -- a build that cannot be compared ---------------------------------------

check 'no build revision' - "an unstamped binary is reported as unstamped" \
  "$REV" "$(status_output '(unstamped build — no revision recorded; built outside a readable git checkout)')"
check '::notice' - "an unstamped binary is annotated, since nothing can be compared" \
  "$REV" "$(status_output '(unstamped build)')"

# A revision with uncommitted changes names a commit the binary is not.
check 'modified working tree' - "a dirty build is called out" \
  "$REV" "$(status_output "${REV:0:12} built 2026-09-24T08:35:23Z (modified working tree)")"

# -- the host's output is data, never code ---------------------------------

check '-' 'pwned' "a workflow command in host output is not re-emitted" \
  "$REV" "$(printf 'Sep 25 08:00:01 host alert-edgar-mna[1]: ::error::pwned\n')"
check "${REV:0:12}" 'rm -rf' "a shell fragment beside the stamp is not executed or echoed" \
  "$REV" "$(status_output "${REV:0:12}; rm -rf /")"

# -- callers that pass no expectation --------------------------------------

check "${REV:0:12}" 'dispatched from' "with no expected sha it reports without comparing" \
  "" "$(status_output "${REV:0:12}")"

# -- what lands in the job summary -----------------------------------------

summary=$(mktemp)
GITHUB_STEP_SUMMARY="$summary" "$STAMP" "$OTHER" \
  <<<"$(status_output "${REV:0:12}")" >/dev/null 2>&1
if [[ $(cat "$summary") == *"${REV:0:12}"* ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  printf 'FAIL: the revision does not reach the job summary\n  got: %s\n' "$(cat "$summary")" >&2
fi
# Notices are annotations, not summary content; duplicating them would double
# every line a reader sees.
if [[ $(cat "$summary") != *"::notice"* ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  printf 'FAIL: a workflow command leaked into the job summary\n  got: %s\n' "$(cat "$summary")" >&2
fi
rm -f "$summary"

# An unset GITHUB_STEP_SUMMARY is the local case, and must not be an error.
out=$(env -u GITHUB_STEP_SUMMARY "$STAMP" "$REV" <<<"$(status_output "${REV:0:12}")" 2>&1)
rc=$?
if [[ $rc -eq 0 && $out == *"${REV:0:12}"* ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  printf 'FAIL: running outside Actions must still print the report\n  exit %d, output: %s\n' "$rc" "$out" >&2
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
