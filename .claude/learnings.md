# Learnings

Durable rules, one or two lines each. The incidents behind them are in git
history and `log-archive.md`. Before adding a rule, check whether an existing
one covers it and sharpen that instead.

## Evidence

- **Read what the bots emitted, not only whether they are up.** A service can be
  `active` and healthy while re-sending the same alerts every 20 seconds.
- **Know which hop produced a result.** `Tunnel connection failed: 403` is this
  session's egress proxy, not the site (`$HTTPS_PROXY/__agentproxy/status`
  explains it). Put a known-good control in any probe.
- **If a question depends on a network this session can't reach, make the host
  report the answer in its normal output** rather than probing from here.
- **A failed command produces zeros, not evidence.** Check that the input
  arrived (size, status, a line that must be present) before trusting a count.
  A zero that confirms what you hoped needs the most scrutiny.
- **Read the code path before inferring from an outcome.** "X must have
  happened" needs the code or the timing to show it. Durations are cheap
  evidence: a 0.2s cycle cannot have fetched a filing.
- **Verify a prediction about a tool's output by running the tool**, not by
  reasoning about what it should say.
- **Work out what period a number sampled before reasoning about the code.** A
  number repeated inside one date-granular window is a constant of that day;
  `0 of 0` on a Monday pre-market is the weekend. Readings from different code
  versions are not comparable.
- **Readings from the same hour of day are one reading.** Record when a window
  was read. Before concluding "never happens" on any service, read its
  cumulative counter in the snapshot; a per-cycle zero at the run hour is not
  evidence.
- **A filter branch that never fires across hundreds of inputs is a parser bug
  until shown otherwise.** Check that the parser produces the field the branch
  reads, against the source's schema.
- **A metric is an instrument only once you have read it and checked the value
  against something you know.** When adding one, write and run the command that
  reads it. Counters reset on every deploy.
- **Instrument the hypothesis and its alternatives.** A zero is a result.
- **Read a funnel as ratios between adjacent stages, not only for zeros.**
  `new` → `fetched` read 1369 → 696 on 2026-09-29 and went unflagged for 3 runs.
- **Before reusing a module, check that it fits the new place's failure mode.**
  The journal reports conditions; counters report rates.

## Tests

- **A test that skips when a dependency is missing lies.** Fail loudly for
  dependencies CI always has.
- **Engine tests own their fixtures** (`internal/engine/testdata/fleet`). Never
  point one at the repo's `fleet/`; `live_fleet_test.go` may assert only what is
  true of any valid fleet.
- **To test "this doesn't repeat", repeat it.** A change detector keyed on a
  string that contains a counter fires every time. A sentinel must be a value
  the domain can't produce.
- **Test counters and log lines, especially when the code around them exits
  early** (early returns, abandoned generators).
- **The CI environment is part of the test.** The `alertctl` job runs the
  validator on bare distro Python on purpose, as the host does.
- **A script on the production path is untested unless CI runs it.**

## Deploy path and infrastructure

- **SSH exit 255 is a transport failure, not empty output** (`checkSSHResult`).
  Every remote read must distinguish "said nothing" from "never ran". `alertctl`
  on the VM needs `sudo` for the same reason.
- **Under `pipefail`, `| head`, `| grep -q` and `| read` can end a script.**
  Capture into a variable and slice it.
- **Handoff steps:** chain with `&&` so a failed `cd` blocks the next command;
  put each step on one line so a pasted prompt prefix fails visibly; name the
  shell in every step.
- **A wildcard in an IAM Deny also denies reads**, and Terraform refreshes before
  it plans. List Deny actions explicitly (`tools/check_iam_denies.py` enforces
  this).
- **`terraform fmt` is not validation.** Only CI's `init` + `validate` checks
  provider arguments.
- **Dependencies are vendored so the control plane builds offline during an
  incident.** `tools/validate.py` is also deploy gate 1, so schema changes affect
  deploys immediately.
- **Explain a diff by its cause, not by a list of what could have caused it.**
  Operator-facing text that is wrong in the common case trains people to skip it.
- **Before asking for a capability, check whether the system already has it**
  (`plan` already rebuilds `alertctl`; the binary already carries its VCS stamp).
- **Before fixing a limitation, check what it is being used for.** `logs`
  returning the oldest end of its window is how we read the post-close hour.

## Git and session

- **After each merge:** `git checkout -B <branch> origin/main`, then
  `git push --force-with-lease -u origin <branch>`. The reset points upstream at
  `origin/main`, so a bare `git push` would target `main`.
- **Run `git branch --show-current` immediately before every commit**, and
  re-check shell state after any denied or failed command.
- **If CI never started, read `mergeable_state`.** A dirty PR gets no run.
- **A backgrounded sleep is not a wait.** Block with an `until` loop. A job's
  archived log can 404 well after the job finishes.
- **Two sessions can run at once.** Re-read `git log origin/main` before writing
  the log or starting a second piece of work; attribute from commits.
- **Check GitHub write access early.** Reads can succeed while push, PR and issue
  creation return 403.

## Authority

- **Don't widen your own boundary.** A change to the mechanism that constrains
  you is a proposal for the owner, even when the argument that the constraint is
  partly illusory is correct.
