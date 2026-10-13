#!/usr/bin/env bash
#
# claims.sh — print the identity claims of a GitHub OIDC token, never the token.
#
#   claims.sh < jwt
#
# AWS answers a trust-policy mismatch with "Not authorized to perform
# sts:AssumeRoleWithWebIdentity" and nothing else: it does not say which
# condition failed or what the token claimed. The only way to name the
# mismatch is to read what GitHub sent and compare it with the `sub` and `aud`
# conditions in infra/terraform/oidc.tf (docs/adr/0001, addendum). This step
# runs before every role assumption so that the comparison is already in the
# log of the run that failed.
#
# Only the payload segment is decoded. The header and the signature are never
# echoed, and without the signature the claims cannot be presented to AWS.
# The claims themselves name the repository, ref and workflow; none is secret.
#
# A reporter, never a verdict: it always exits 0. It cannot know what the
# trust policy accepts, and a diagnostic that fails the job would hide the
# real failure behind its own.

set -uo pipefail

token=$(cat)

# A JWT is header.payload.signature, each base64url without padding.
IFS=. read -r _ payload _ <<<"$token"
if [[ -z ${payload-} ]]; then
  echo "::warning::OIDC claims: no token to decode (the request for one failed or returned nothing)"
  exit 0
fi

payload=${payload//-/+}
payload=${payload//_//}
while (( ${#payload} % 4 )); do payload+="="; done

if ! json=$(base64 -d <<<"$payload" 2>/dev/null) || ! jq -e . >/dev/null 2>&1 <<<"$json"; then
  echo "::warning::OIDC claims: the token's payload is not base64url JSON"
  exit 0
fi

# The claims a trust policy can condition on, in the order a reader compares
# them. `sub` is the one that has changed format before.
for claim in sub aud ref repository repository_id repository_owner_id job_workflow_ref; do
  value=$(jq -r --arg c "$claim" '.[$c] // "(absent)" | if type == "array" then join(",") else tostring end' <<<"$json")
  printf '%-20s %s\n' "$claim" "$value"
done

sub=$(jq -r '.sub // "(absent)"' <<<"$json")
echo "::notice title=OIDC subject::$sub"
exit 0
