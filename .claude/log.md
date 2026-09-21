# Run log

One entry per scheduled run, newest last. Keep entries short.

Format:

    ## YYYY-MM-DD — <item or "no-op">
    - Did: ...
    - PR: #N, merged / open (why) / needs-conan (or none, and why)
    - Verification: green / what failed
    - Next: what the next run should pick up
    - Notes: anything surprising (also add to learnings.md if durable)
    - Catch-up: one sentence Conan should read even if he reads nothing else

## 2026-09-21 — baseline (set up by hand, not a scheduled run)
- Did: added CLAUDE.md and .claude/ backlog, log, learnings.
- Verification on `e44bd09`: go vet, ruff, validate, observability check, and
  29 pytest tests pass; `internal/audit`, `console`, `exec`, `fleet` pass;
  `internal/engine` fails 2 tests (ref coupling, see backlog #1).
  golangci-lint not run.
- Next: backlog #1.
- Switched to full auto-merge behind required CI checks (CLAUDE.md §2).
- Brief revised: agent now releases and deploys through the gates as the
  `claude-ops` operator identity and reports on production every run; work is
  sized per run by milestone, not by diff size.
- Moved to a cloud routine. Production access is now GitHub Actions with
  OIDC → IAM → SSM; there are no SSH keys and no credentials in the agent
  (CLAUDE.md §6).

## 2026-09-21 — backlog P0 #1: make `main` green
- Did: **two** fixes, because `main` turned out to be red for two unrelated
  reasons — CI run #6 on 2026-08-20 already failed, two commits before the
  `e44bd09` ref bump the backlog blamed. The first fix was the known one:
  gave `internal/engine` its own fixture fleet
  (`internal/engine/testdata/fleet`, four fictional services pinned at
  `v1.0.0`) and moved every test in the package onto it, so rolling
  `source.ref` can no longer fail a test about rendering or deploy
  mechanics. Added `live_fleet_test.go`, which keeps the real `fleet/` under
  test using only properties true of any valid fleet. Documented the
  convention in `docs/runbooks/local-development.md`. The second fix was the
  hidden one: `schema/service.schema.json` had `$id: alertplatform/v1/service`,
  a bare relative path that old `jsonschema` resolvers dereference as a URL
  (`unknown url type: 'alertplatform/v1/alertplatform/v1/service'`). It only
  showed in the `alertctl` CI job, the one with no `pip install jsonschema`,
  where the rollback test shells out to `tools/validate.py` as the real apply
  gate and gets the distro package. `$id` is now `urn:alertplatform:v1:service`;
  `services/tests/test_schema.py` pins that it stays absolute and that every
  `$ref` stays local. Fixed in the schema rather than the workflow because
  that gate runs on the host at deploy time, against the host's library — a
  schema needing a fetch to validate is a gate that fails when the network is
  least trustworthy. Third commit restores
  `.claude/` (backlog, log, learnings) into git, where a fresh clone can see
  it, and drops the 1.9 MB `alert-platform-launchpad.zip` that carried it.
- PR: #1, **merged** (squash). Auto-merge is not enabled on this repository
  (Settings → General → Allow auto-merge), and CLAUDE.md §2 forbids changing
  repo settings, so it was merged by hand once all four checks were green —
  never on a pending or failing check. CI run #15 on `main` is green, the
  first since 2026-08-20.
  Blocked for part of the run: `git push` returned 403 and the GitHub API
  refused branch, PR *and issue* creation with "Resource not accessible by
  integration", while every read succeeded — so the §7 handoff could not be
  filed as an issue either and went out as the run notification instead.
  Conan granted the Claude GitHub App access to the repo mid-run and the
  push went through unchanged.
- Verification: green. `go vet`, `gofmt -l cmd internal` (empty),
  `go test ./... -race` (5/5 packages), `go build`, `ruff check`,
  `tools/validate.py` against both the live and the fixture fleet,
  `gen_observability.py --check`, `pytest services/tests -q` (31 passed).
  The `$id` fix was reproduced against `jsonschema==4.17.3` (the last
  `RefResolver` release, matching the runner) and re-verified under both
  resolver generations, including the whole Go suite with the old one first
  on `PATH` — the failing CI condition exactly. All four CI jobs green on the
  branch.
  `golangci-lint` not run locally — installed v2.5.0 vs. a v1-format config;
  backlog has it as an environment item. It did run in CI and passed. Also confirmed the fix does what it claims
  by bumping `edgar-mna` to `v9.9.9` locally: engine tests stayed green,
  which is precisely what `e44bd09` broke.
- Production report: **none possible.** `observe.yml` does not exist yet
  (backlog P0 #3), and the routine has no other read path to the host, so
  there is no evidence about the four services this run. CLAUDE.md §6 forbids
  deploying until that pipeline and its handoff are done, so nothing was
  deployed and no release was tagged. Nothing in the docs should claim
  otherwise.
- Next: P0 #2 (module path
  `conanohara` → `conan0h`) and P0 #3 (the OIDC → IAM → SSM pipeline), which
  is what unblocks every production claim in this repo. Also worth an early
  look: new backlog item 8a, pinning the validator's Python dependencies so
  the apply gate cannot fail on whatever `jsonschema` the host has.
- Notes: `rollback_test.go` skips when `pyyaml`/`jsonschema` are absent, so
  half of backlog #1 was invisible locally. Separately, the check-runs API
  reported the failing `alertctl` job as still running for ~9 minutes after
  it had failed; reading the job logs gave the truth. Both in learnings,
  along with the `$id` lesson and "one red check can hide another".
  Enabling auto-merge would remove a manual step from every future run, but
  it is Conan's call: it is a repo setting, which §2 puts out of bounds.
- Catch-up: `main` had been red since 2026-08-20 for two unrelated reasons,
  not one — PR #1 fixes both, and future releases can no longer break the
  engine tests. Still no production access, so this run makes no claims about
  the host.

