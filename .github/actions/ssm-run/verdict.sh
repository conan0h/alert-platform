#!/usr/bin/env bash
#
# verdict.sh — decide whether a host exit code failed the run.
#
#   verdict.sh <ssm-status> <host-exit-code> <finding-codes> <document> <instance>
#
# Its own file, rather than inline in action.yml, for two reasons. It is on the
# path to production and a script CI cannot run is a script nobody has tested —
# the lesson of eight deploy-path bugs in one week. And taking its arguments as
# arguments means the finding set is never spliced into a shell by GitHub's
# ${{ }} expansion.
#
# The distinction it exists to draw: SSM knows only "exit code was non-zero"
# and calls all of it Failed. But `alertctl drift` exits 3 to mean it worked
# and found drift. Reporting that as a failed run makes an hourly job red for
# as long as the fleet is behind its specs, and a signal that is always red is
# a signal nobody reads when it goes red for a real reason.

set -euo pipefail

status=${1:?ssm status required}
code=${2-}
findings=${3-}
document=${4:-the document}
instance=${5:-the instance}

if [[ $status == Success ]]; then
  exit 0
fi

# A malformed finding set fails the run rather than being read generously: it
# is a misconfiguration, and the safe reading of "I could not parse which
# codes are benign" is that none of them are.
if [[ -n $findings && ! $findings =~ ^[0-9[:space:]]+$ ]]; then
  echo "::error::finding-exit-codes must be space-separated numbers, got '$findings'"
  exit 1
fi

# shellcheck disable=SC2086  # unquoted on purpose, to split into words
for finding in $findings; do
  if [[ -n $code && $code == "$finding" ]]; then
    echo "::warning::$document reported a finding on $instance (exit $code). The host answered the question; read its output above."
    echo "finding=true" >> "${GITHUB_OUTPUT:-/dev/null}"
    exit 0
  fi
done

echo "::error::$status on $instance running $document (host exit ${code:-unknown})"
exit 1
