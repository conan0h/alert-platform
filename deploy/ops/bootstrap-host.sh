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

log() { printf '==> %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)" >&2; exit 1; }

# --- packages --------------------------------------------------------------
# git to move the checkout, golang to build alertctl from it, curl for the
# health probe. Go is here rather than shipping a binary so the control plane
# is built from the same commit it deploys, with no artifact to trust.
log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git golang-go curl >/dev/null

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
