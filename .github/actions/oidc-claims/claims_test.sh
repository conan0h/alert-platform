#!/usr/bin/env bash
#
# Tests for claims.sh — what a token claimed, without the token.
#
# The cases that matter: the subject is printed and annotated, the signature
# never appears in the output, and a missing or malformed token is reported
# without failing the job.
#
#   ./.github/actions/oidc-claims/claims_test.sh

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CLAIMS="$HERE/claims.sh"

SUB='repo:conan0h@98814385/alert-platform@1340575956:ref:refs/heads/main'
SIG='stubSignatureMustNotAppear'

b64url() { base64 -w0 | tr '+/' '-_' | tr -d '='; }

# A payload whose base64 needs padding and contains both url-safe characters,
# so a decoder that skips either step fails here.
PAYLOAD=$(printf '{"sub":"%s","aud":"sts.amazonaws.com","ref":"refs/heads/main","repository_id":"1340575956","x":"??>>"}' "$SUB" | b64url)
HEADER=$(printf '{"alg":"RS256","kid":"stub"}' | b64url)
TOKEN="$HEADER.$PAYLOAD.$SIG"

pass=0; fail=0

# check <want-substring|-> <must-not-contain|-> <desc> <stdin>
check() {
  local want=$1 forbid=$2 desc=$3 input=$4
  local out rc
  out=$("$CLAIMS" <<<"$input" 2>&1); rc=$?
  if [[ $rc -ne 0 ]]; then
    fail=$((fail + 1))
    printf 'FAIL: %s\n  claims.sh exited %d (it must always exit 0)\n  output: %s\n' "$desc" "$rc" "$out" >&2
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

check "::notice title=OIDC subject::$SUB" - "the subject is annotated" "$TOKEN"
check 'aud                  sts.amazonaws.com' - "the audience is printed" "$TOKEN"
check 'repository_owner_id  (absent)' - "a missing claim says so instead of printing a blank" "$TOKEN"
check - "$SIG" "the signature is never printed" "$TOKEN"
check - "$HEADER" "the header is never printed" "$TOKEN"

check '::warning::' '::notice' "no token is a warning, not a failure" ''
check '::warning::' '::notice' "a token whose payload is not JSON is a warning" "$HEADER.bm90IGpzb24.$SIG"

printf '%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
