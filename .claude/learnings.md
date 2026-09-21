# Learnings

Hard-won facts future runs should not have to rediscover. Append; don't
rewrite history.

- **SSH exit 255 is a transport failure, not an answer.** It was once read as
  "empty output", so `plan` saw an empty fleet and proposed creating every
  service. Fixed in `internal/exec/runner.go` (`checkSSHResult`, covered by
  `ssh255_test.go`). Any new remote read must distinguish "the command said
  nothing" from "the command never ran".
- **`alertctl` on the VM needs `sudo`.** Without it, reads of root-owned state
  come back empty and look like an empty fleet: the live reproduction of the
  255 class of bug.
- **Secrets resolve on the remote host, over SSH.** Local AWS env vars (even
  through `sudo -E`) are irrelevant; the host's credentials are what matter.
  Hence the IAM instance role.
- **Drift compares refs and unit hashes only.** In-place edits inside a
  release directory are invisible (backlog #6).
- **Tests that load the live `fleet/` specs are coupled to deployment
  decisions.** A ref bump in `e44bd09` broke two engine tests. Tests should own
  their fixtures.
- **Dependencies are vendored on purpose**: the control plane must build
  without network access during an incident. Always `go mod vendor`.
- **Validator and deploy gate are the same code** (`tools/validate.py`), so
  schema changes affect deploys immediately.
- **A test that skips when a dependency is missing is a test that lies.**
  `rollback_test.go` skips unless `pyyaml` and `jsonschema` import, so the
  ref-coupling bug in backlog #1 looked like one failing test locally and
  was two in CI. Prefer failing loudly over skipping when the dependency is
  something CI always has.
- **Engine tests own their fleet.** `internal/engine/testdata/fleet` pins
  `v1.0.0` forever. Never point an engine test at the repo's `fleet/`: those
  specs are deployment decisions, and rolling a ref is supposed to be
  routine. The one exception is `live_fleet_test.go`, which may assert only
  properties true of any valid fleet — no version, port, service name or
  count.
- **The routine's GitHub access can be read-only, and it fails late.** Reads
  (`get_me`, listing PRs and tags) all succeed while `git push`, branch
  creation, PR creation *and issue creation* return 403. So the §7 handoff
  route is not always available: if issues are refused too, the run
  notification is the only channel to Conan. Check for write access early in
  a run rather than discovering it after the milestone is built.
- **`.claude/` must be committed.** The directory arrived inside an uploaded
  zip rather than in git, so the log and learnings were invisible to a fresh
  clone — which is every run. Memory that is not in the repo does not exist.

