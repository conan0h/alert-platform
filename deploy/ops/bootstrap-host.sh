#!/usr/bin/env bash
#
# bootstrap-host.sh — prepare this host to be deployed to by the pipeline.
#
# Run once, as root, over SSM Session Manager. Idempotent: running it again
# is a no-op that re-asserts ownership and permissions, which is the point —
# it is also the repair procedure if someone has changed something by hand.
#
# It creates the account the SSM documents run as, the sudo rule that lets
# that account run exactly one program, and the checkout that program reads
# desired state from. It grants nothing else.
#
#   sudo bash bootstrap-host.sh
#
# What it deliberately does NOT do: touch any running service, write any
# secret, or change a firewall rule. Bootstrapping the deploy path and
# deploying are different operations, and this is the first one.

set -euo pipefail

readonly REPO_URL=https://github.com/conan0h/alert-platform
readonly CHECKOUT=/opt/alert-platform/control-plane
readonly WRAPPER_SRC_REL=deploy/ops/alert-deploy
readonly WRAPPER=/usr/local/sbin/alert-deploy
readonly OPS_USER=alert-ops
readonly SVC_USER=svc-alerts
readonly PLAN_DIR=/var/lib/alert-platform/plans
readonly BIN_DIR=/usr/local/lib/alert-platform
readonly SUDOERS=/etc/sudoers.d/alert-ops
# Must track the `go` directive in go.mod.
readonly GO_MIN_VERSION=1.22

log() { printf '==> %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)" >&2; exit 1; }

# --- am I on the right machine? --------------------------------------------
#
# This script creates system accounts and writes a sudoers file. Run on the
# wrong host that is at best litter and at worst a privilege grant somewhere
# nobody is expecting one, so it refuses before touching anything rather than
# failing partway through on a missing command.
#
# The mistake this catches is an easy one: the handoff has the operator in AWS
# CloudShell for the Terraform steps and in SSM Session Manager for these, and
# the two shells look alike. CloudShell is Amazon Linux, which has no apt-get,
# so the first symptom used to be a confusing "apt-get: command not found"
# after the script had already announced it was installing packages.
if ! command -v apt-get >/dev/null 2>&1; then
  cat >&2 <<'WRONG_HOST'
This is not the alert-platform host.

bootstrap-host.sh expects Ubuntu on the EC2 instance that runs the fleet, and
this machine has no apt-get — so it is most likely AWS CloudShell, which is
Amazon Linux and is where the Terraform steps run, not this one.

Open a session on the host instead:
  https://console.aws.amazon.com/systems-manager/session-manager/start-session

The prompt there looks like `ubuntu@ip-…`. Nothing has been changed here.
WRONG_HOST
  exit 1
fi

# Belt and braces: the SSM agent's registration names the instance this script
# is meant for. Absent (not an EC2 instance) is a warning rather than an error,
# because a future host may be provisioned differently; a *mismatch* is not.
readonly EXPECTED_INSTANCE=i-06aaf8cca765d5352
if [[ -r /var/lib/amazon/ssm/registration ]]; then
  here=$(sed -n 's/.*"ManagedInstanceID":"\([^"]*\)".*/\1/p' /var/lib/amazon/ssm/registration)
  if [[ -n $here && $here != "$EXPECTED_INSTANCE" ]]; then
    echo "This is instance $here, but this script is for $EXPECTED_INSTANCE." >&2
    echo "Refusing to create accounts and sudo rules on the wrong host." >&2
    echo "If the fleet has moved, update EXPECTED_INSTANCE in this script." >&2
    exit 1
  fi
fi

# --- packages --------------------------------------------------------------
# git to move the checkout, curl for the health probe.
log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl >/dev/null

# Go, to build alertctl from the same commit it deploys — no artifact to
# trust, and the vendored deps mean that build needs no network.
#
# Not `apt-get install golang-go`: on 22.04 that is Go 1.18, and go.mod
# requires 1.22, so the first `plan` would die on a toolchain error long after
# this script reported success. Not a tarball from go.dev either, because then
# this script is asserting a URL and a checksum nobody verified.
#
# The snap is the mechanism this host already runs (its SSM agent is one), and
# /snap/bin is in sudo's secure_path on Ubuntu, so the wrapper can find it.
log "installing Go"
if ! command -v snap >/dev/null 2>&1; then
  echo "FAILED: snap is not available; install Go >= $GO_MIN_VERSION another way and re-run" >&2
  exit 1
fi
if ! command -v go >/dev/null 2>&1; then
  snap install go --classic
fi

# Assert rather than assume. A Go too old to build the module is the failure
# this whole block exists to prevent, and finding out here costs a minute
# where finding out at deploy time costs an incident.
go_version=$(go version 2>/dev/null | awk '{print $3}' | sed 's/^go//')
if [[ -z $go_version ]]; then
  echo "FAILED: go is installed but 'go version' produced nothing" >&2
  exit 1
fi
if ! printf '%s\n%s\n' "$GO_MIN_VERSION" "$go_version" | sort -V -C; then
  echo "FAILED: go $go_version is older than the $GO_MIN_VERSION that go.mod requires" >&2
  echo "Try: snap refresh go --classic" >&2
  exit 1
fi
log "Go $go_version (needs >= $GO_MIN_VERSION)"

# --- accounts --------------------------------------------------------------
# alert-ops owns nothing and runs nothing except through sudo. It exists so
# the SSM documents have an identity that is not root and not the service
# account, which keeps "what the pipeline may do" separate from "what the
# services may touch".
log "creating $OPS_USER"
if ! id -u "$OPS_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$OPS_USER"
fi

log "ensuring $SVC_USER exists"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --shell /usr/sbin/nologin "$SVC_USER"
fi

# --- the checkout ----------------------------------------------------------
# Root-owned. The wrapper refuses to run if it is writable by anyone else,
# because a writable checkout is a way to make the control plane deploy
# something that was never reviewed.
log "preparing $CHECKOUT"
install -d -o root -g root -m 0755 /opt/alert-platform
if [[ ! -d $CHECKOUT/.git ]]; then
  git clone --quiet "$REPO_URL" "$CHECKOUT"
fi
git -C "$CHECKOUT" remote set-url origin "$REPO_URL"
git -C "$CHECKOUT" fetch --quiet --tags origin main
git -C "$CHECKOUT" reset --quiet --hard origin/main
chown -R root:root "$CHECKOUT"
chmod -R go-w "$CHECKOUT"

install -d -o root -g root -m 0755 "$BIN_DIR"
install -d -o root -g root -m 0750 "$PLAN_DIR"

# Build alertctl here, so the host is usable the moment this script finishes
# rather than only after its first plan. It also turns the Go check above from
# "the version string looks right" into "this toolchain compiles this module",
# which is the thing actually being relied on.
log "building alertctl"
# -C, because this script runs from /tmp and `go build /abs/path/to/pkg`
# resolves the package against the current directory's module.
env GOFLAGS=-mod=vendor go build -C "$CHECKOUT" -o "$BIN_DIR/alertctl" ./cmd/alertctl
"$BIN_DIR/alertctl" 2>&1 | head -n 1
log "alertctl built and runnable"

# --- the wrapper -----------------------------------------------------------
# Copied out of the checkout rather than symlinked into it: a symlink would
# mean "whatever the checkout currently says" and the sudo rule would no
# longer name a fixed program. Re-run this script to pick up a new version,
# which is a deliberate, logged-in-as-root act.
log "installing $WRAPPER"
install -o root -g root -m 0755 "$CHECKOUT/$WRAPPER_SRC_REL" "$WRAPPER"

# --- sudo ------------------------------------------------------------------
# One program, no password, no environment carried across. This is the entire
# privilege grant; everything the pipeline can do on this host is whatever
# alert-deploy permits, and nothing else.
log "installing $SUDOERS"
cat > "$SUDOERS" <<SUDOERS_EOF
# Managed by deploy/ops/bootstrap-host.sh — do not edit by hand.
# $OPS_USER may run exactly one program as root, and pass no environment to it.
Defaults:$OPS_USER !requiretty
Defaults:$OPS_USER env_reset
$OPS_USER ALL=(root) NOPASSWD: $WRAPPER
SUDOERS_EOF
chmod 0440 "$SUDOERS"

# A malformed sudoers file locks the host out of its own deploy path, so it is
# checked before we walk away from it. visudo -c reads every file in the set.
if ! visudo -c -q; then
  rm -f "$SUDOERS"
  echo "sudoers validation failed; removed $SUDOERS and changed nothing else" >&2
  exit 1
fi

# --- state -----------------------------------------------------------------
log "ensuring state directories"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 /var/lib/alert-platform
install -d -o root -g root -m 0755 /var/log/alert-platform
install -d -o root -g "$SVC_USER" -m 0750 /etc/alert-platform

# --- verify ----------------------------------------------------------------
# Prove the path works before reporting success, so a failure is found here
# rather than by the first deploy.
log "verifying the sudo path"
# `sudo -l <cmd>` answers exactly the question being asked — may this user run
# this program? — without running it. Inferring permission from the wrapper's
# own exit code would conflate "sudo refused" with "the wrapper rejected the
# arguments", which are different failures with different fixes.
if runuser -u "$OPS_USER" -- sudo -n -l "$WRAPPER" >/dev/null 2>&1; then
  log "OK: $OPS_USER may run $WRAPPER"
else
  echo "FAILED: sudo does not permit $OPS_USER to run $WRAPPER" >&2
  exit 1
fi

log "verifying the wrapper actually runs and refuses a bad verb"
if runuser -u "$OPS_USER" -- sudo -n "$WRAPPER" help >/dev/null 2>&1; then
  log "OK: $WRAPPER runs under sudo"
else
  echo "FAILED: $WRAPPER did not run cleanly under sudo" >&2
  exit 1
fi

log "verifying the grant is narrow"
if runuser -u "$OPS_USER" -- sudo -n -l /bin/true >/dev/null 2>&1; then
  echo "FAILED: $OPS_USER may sudo something other than the wrapper" >&2
  exit 1
fi
log "OK: $OPS_USER may sudo nothing else"

cat <<'DONE'

Bootstrap complete. This host can now be driven by the deploy pipeline.

Nothing has been deployed. The next step is a plan from the deploy workflow;
until then the fleet is exactly as it was before this script ran.
DONE
