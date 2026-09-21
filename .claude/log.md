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
- Did: gave `internal/engine` its own fixture fleet
  (`internal/engine/testdata/fleet`, four fictional services pinned at
  `v1.0.0`) and moved every test in the package onto it, so rolling
  `source.ref` can no longer fail a test about rendering or deploy
  mechanics. Added `live_fleet_test.go`, which keeps the real `fleet/` under
  test using only properties true of any valid fleet. Documented the
  convention in `docs/runbooks/local-development.md`. Second commit restores
  `.claude/` (backlog, log, learnings) into git, where a fresh clone can see
  it, and drops the 1.9 MB `alert-platform-launchpad.zip` that carried it.
- PR: #1, opened on `claude/awesome-pasteur-jzcf56`, auto-merge on squash.
  Blocked for part of the run: `git push` returned 403 and the GitHub API
  refused branch, PR *and issue* creation with "Resource not accessible by
  integration", while every read succeeded — so the §7 handoff could not be
  filed as an issue either and went out as the run notification instead.
  Conan granted the Claude GitHub App access to the repo mid-run and the
  push went through unchanged.
- Verification: green. `go vet`, `gofmt -l cmd internal` (empty),
  `go test ./... -race` (5/5 packages), `go build`, `ruff check`,
  `tools/validate.py` against both the live and the fixture fleet,
  `gen_observability.py --check`, `pytest services/tests -q` (29 passed).
  `golangci-lint` not run — installed v2.5.0 vs. a v1-format config; backlog
  has it as an environment item. Also confirmed the fix does what it claims
  by bumping `edgar-mna` to `v9.9.9` locally: engine tests stayed green,
  which is precisely what `e44bd09` broke.
- Production report: **none possible.** `observe.yml` does not exist yet
  (backlog P0 #3), and the routine has no other read path to the host, so
  there is no evidence about the four services this run. CLAUDE.md §6 forbids
  deploying until that pipeline and its handoff are done, so nothing was
  deployed and no release was tagged. Nothing in the docs should claim
  otherwise.
- Next: confirm #1 merged and `main` green, then P0 #2 (module path
  `conanohara` → `conan0h`) and P0 #3 (the OIDC → IAM → SSM pipeline), which
  is what unblocks every production claim in this repo.
- Notes: `rollback_test.go` skips when `pyyaml`/`jsonschema` are absent, so
  half of backlog #1 was invisible locally. Added to learnings.
- Catch-up: `main` was red since `e44bd09`; PR #1 fixes it for good by
  giving the engine tests their own fleet, so future releases cannot break
  them. Still no production access, so this run makes no claims about the
  host.

