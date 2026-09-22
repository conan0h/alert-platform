#!/usr/bin/env bash
#
# Tests for the shell the SSM documents run on the host.
#
# Those scripts live inside `yamlencode` in infra/terraform/ssm_documents.tf,
# which means `terraform validate` checks that the HCL is well-formed and
# nothing checks the shell at all. They are on the production read and write
# paths, and the bug they are most likely to have is handing a verb an argument
# it refuses — which shows up as a failed deploy, not as a failed plan.
#
# So the scripts are extracted from the .tf at test time rather than copied
# here: a copy would drift, and a drifted copy of a security-relevant script is
# worse than no copy. The extraction is deliberately literal, and it fails
# loudly if the heredoc it expects is no longer there.
#
#   ./deploy/ops/ssm_scripts_test.sh

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
TF="$ROOT/infra/terraform/ssm_documents.tf"

pass=0
fail=0

ok()   { printf 'ok   %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf 'FAIL %s\n     %s\n' "$1" "$2"; fail=$((fail + 1)); }

[[ -f $TF ]] || { echo "missing $TF" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Extract one `<name>_script = <<-SCRIPT ... SCRIPT` heredoc body and
# substitute the one Terraform interpolation it contains. Anything else
# interpolated would arrive here as literal `${...}` and the stub would fail on
# it, which is the intended behaviour: this test should break when the script
# grows a dependency it does not describe.
extract() {
  local name=$1 out=$2
  python3 - "$TF" "$name" > "$out" <<'PY'
import re, sys
tf, name = sys.argv[1], sys.argv[2]
text = open(tf).read()
m = re.search(rf"{name}\s*=\s*<<-SCRIPT\n(.*?)\n\s*SCRIPT", text, re.DOTALL)
if not m:
    sys.exit(f"could not find the {name} heredoc in {tf}")
body = m.group(1)
body = body.replace("${var.ops_user}", "alert-ops")
if "${" in body:
    sys.exit(f"{name} still contains an un-substituted interpolation:\n{body}")
# The heredoc is indented in the .tf; <<- strips it on the host, so strip it here.
lines = [ln[4:] if ln.startswith("    ") else ln for ln in body.splitlines()]
print("\n".join(lines))
PY
}

# Stubs for the three things the script reaches for. Each one records its
# argv verbatim, so a test can assert on exactly what the wrapper would have
# been handed — including whether an argument was passed at all.
mkdir -p "$WORK/bin" "$WORK/usr/local/sbin"
cat > "$WORK/bin/runuser" <<'STUB'
#!/usr/bin/env bash
# runuser -u <user> -- <cmd...>   → drop the first three args and run the rest
shift 2; shift
exec "$@"
STUB
cat > "$WORK/bin/sudo" <<'STUB'
#!/usr/bin/env bash
# sudo -n <cmd...> → drop the flag and run the rest
[[ ${1:-} == -n ]] && shift
exec "$@"
STUB
cat > "$WORK/usr/local/sbin/alert-deploy" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@"
STUB
chmod +x "$WORK/bin/runuser" "$WORK/bin/sudo" "$WORK/usr/local/sbin/alert-deploy"

# The scripts name the wrapper by absolute path, so the stub has to be reachable
# at that path. A bind mount needs root; a PATH entry will not shadow an
# absolute path. So rewrite that one literal, and assert the rewrite happened —
# if the path in the .tf changes, this test must not silently stop covering it.
prepare() {
  local src=$1 out=$2
  if ! grep -q '/usr/local/sbin/alert-deploy' "$src"; then
    echo "script does not invoke /usr/local/sbin/alert-deploy; the stub would not be used" >&2
    return 1
  fi
  sed "s#/usr/local/sbin/alert-deploy#$WORK/usr/local/sbin/alert-deploy#g" "$src" > "$out"
  chmod +x "$out"
}

run_script() {
  local script=$1; shift
  PATH="$WORK/bin:$PATH" bash "$script" "$@" 2>&1
}

# -- observe ---------------------------------------------------------------

extract observe_script "$WORK/observe.raw" || exit 1
prepare "$WORK/observe.raw" "$WORK/observe.sh" || exit 1

got=$(run_script "$WORK/observe.sh" logs "gha:1" "10 minutes ago")
want=$'logs\n--since\n10 minutes ago\n--actor\ngha:1'
if [[ $got == "$want" ]]; then
  ok "logs receives --since, and the window stays one argument"
else
  bad "logs receives --since, and the window stays one argument" "got: $(printf '%q' "$got")"
fi

# The window must survive as a single argv element. A quoting slip here turns
# "10 minutes ago" into three arguments and the wrapper refuses the run, which
# is safe but reads as a broken read verb.
if [[ $(run_script "$WORK/observe.sh" logs "gha:1" "10 minutes ago" | grep -c .) -eq 5 ]]; then
  ok "the window is not split into separate arguments"
else
  bad "the window is not split into separate arguments" \
      "expected exactly 5 argv elements"
fi

# Every other verb refuses an unexpected argument, so it must not be given one.
for verb in status drift history health; do
  got=$(run_script "$WORK/observe.sh" "$verb" "gha:2" "1 hour ago")
  want=$verb$'\n--actor\ngha:2'
  if [[ $got == "$want" ]]; then
    ok "$verb is not given --since"
  else
    bad "$verb is not given --since" "got: $(printf '%q' "$got")"
  fi
done

# -- deploy ----------------------------------------------------------------

extract deploy_script "$WORK/deploy.raw" || exit 1
prepare "$WORK/deploy.raw" "$WORK/deploy.sh" || exit 1

got=$(run_script "$WORK/deploy.sh" apply "gha:3" "54993f27b007")
want=$'apply\n54993f27b007\n--actor\ngha:3'
if [[ $got == "$want" ]]; then
  ok "apply receives the plan id as a positional argument"
else
  bad "apply receives the plan id as a positional argument" "got: $(printf '%q' "$got")"
fi

got=$(run_script "$WORK/deploy.sh" plan "gha:4" "")
want=$'plan\n--actor\ngha:4'
if [[ $got == "$want" ]]; then
  ok "plan is not given a plan id"
else
  bad "plan is not given a plan id" "got: $(printf '%q' "$got")"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
