#!/usr/bin/env bash
#
# stamp.sh — say which alertctl answered, and whether it is the commit this
# run was dispatched from.
#
#   stamp.sh <expected-sha> < host-stdout
#
# The host builds alertctl from its own checkout and only `plan` moves that
# checkout, so a read verb can answer from a binary several commits old. The
# answer looks identical either way: on 2026-09-22 a `drift` exit 0 was read
# as "production matches main" when it meant "production matches the specs the
# host last synced". `status` and `drift` now print the binary's own revision
# as their first line; this turns that line into a comparison against the
# commit the workflow ran from, which is the part a reader cannot do from
# memory.
#
# It is a reporter, never a verdict: it always exits 0. A stale control plane
# is a normal state between a merge and the next plan, and failing the hourly
# observe run for it would make a red X mean nothing — the same reasoning as
# the finding codes in verdict.sh (docs/adr/0002-exit-codes.md).
#
# Its own file, not inline in action.yml, so CI can run it and so the host's
# output reaches it on stdin rather than through a ${{ }} expansion.

set -euo pipefail

expected=${1-}

# The host's stdout is untrusted input: anything a service logged can appear
# in it. Nothing read here is ever executed, and only text matched against a
# hex pattern is echoed back into a workflow command.
stdout=$(cat)

# The prefix is fixed by buildinfo.Stamp.Line() and pinned by a Go test.
# Bash rather than `grep | head`: under `pipefail` a closed pipe would end
# this script, which is bug #7 in learnings.md.
line=""
while IFS= read -r candidate; do
  if [[ $candidate == "control plane: alertctl "* ]]; then
    line=${candidate#"control plane: alertctl "}
    break
  fi
done <<<"$stdout"

# `logs` and `health` never run alertctl, so there is nothing to report and
# saying so every hour would be noise.
if [[ -z $line ]]; then
  exit 0
fi

rev=""
if [[ $line =~ ^([0-9a-fA-F]{7,40}) ]]; then
  rev=$(tr '[:upper:]' '[:lower:]' <<<"${BASH_REMATCH[1]}")
fi

note() { echo "::notice::$*"; }
say() {
  echo "$*"
  echo "$*" >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
}

if [[ -z $rev ]]; then
  say "Control plane: alertctl reported no revision."
  note "The host's alertctl carries no revision, so nothing can say which commit answered. It was built outside a readable git checkout; a 'deploy.yml step=plan' rebuilds it from the host's checkout."
  exit 0
fi

if [[ $line == *"modified working tree"* ]]; then
  note "The host's alertctl was built from a modified working tree at $rev, so the host is running control-plane code that no commit contains."
fi

if [[ -z $expected ]]; then
  say "Control plane: alertctl $rev."
  exit 0
fi

expected=$(tr '[:upper:]' '[:lower:]' <<<"$expected")
# Either may be the abbreviation, so compare on the shorter of the two.
short=$rev long=$expected
if ((${#rev} > ${#expected})); then
  short=$expected long=$rev
fi

if [[ $long == "$short"* ]]; then
  say "Control plane: alertctl $rev — the commit this run was dispatched from."
else
  say "Control plane: alertctl $rev, but this run was dispatched from $expected."
  note "The host's alertctl is built from $rev; this run was dispatched from $expected. Read verbs never rebuild it, so this answer describes $rev. 'deploy.yml step=plan' syncs the host checkout to origin/main and rebuilds."
fi
