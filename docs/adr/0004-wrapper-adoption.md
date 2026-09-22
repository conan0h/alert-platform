# ADR 0004 — The agent owns the wrapper's verb set; a frozen adopter owns the rules

**Status:** accepted by the owner 2026-09-22; **not yet implemented.** The
installer this describes has not been written. See *Implementation status* below
before relying on any of it.
**Changes the privilege boundary.** Read with ADR 0001, which established the
deploy path, and ADR 0003, which moved AWS ownership.

## Context

`/usr/local/sbin/alert-deploy` is the deploy wrapper. It runs as root through a
sudoers rule that names exactly one program, and it accepts a fixed verb set
(`plan | apply | status | drift | history | health | logs`). Everything the
pipeline can do on this host is whatever that file permits.

The file on the host is a *copy*, installed by `deploy/ops/bootstrap-host.sh`
from the checkout. So changing the verb set — adding the `alerts` read verb that
the alert archive needs (backlog #27), or bounding `logs` with `-n` so it returns
the newest entries rather than the oldest (backlog #32) — required a human with
root to re-run bootstrap. That was the last recurring handoff.

Backlog #24 asked whether that should move. Two facts shaped the answer.

**The first cuts in favour.** The wrapper is not actually confining the agent's
code. It runs as root and invokes `alertctl`, which `plan` rebuilds from the
checkout on every run. Go code the agent wrote therefore already executes as
root on this host. What the wrapper confines is the *verb surface reachable from
CI*, which is a different and narrower thing than it appears.

**The second cuts against, and is decisive.** The SSM document's `allowedValues`
constrains verb *names*, not verb *bodies*. A `logs` verb whose body had been
rewritten would satisfy every `allowedValues` check there is. So the SSM layer is
not a substitute for the wrapper, and letting the pipeline replace the wrapper
wholesale would mean the allowlist is authored by the thing it constrains. That
is not narrowing a privilege; it is removing a layer.

A third consideration is about timing rather than mechanism. Once ADR 0003's
infra role is applied, the agent can change the SSM documents itself — which
means it could add an adoption path unilaterally. That is precisely why this was
put to the owner before the means existed rather than after.

## Decision

Split the wrapper into two things that were previously one.

**The agent owns the wrapper's content**, including its verb set. A verb change
ships as an ordinary PR, merges behind green CI, and reaches the host through an
explicit adoption step.

**A separate program owns the rules a candidate must satisfy.**
`/usr/local/sbin/alert-adopt-wrapper` is installed once by the owner, has its own
sudoers line, and is intended to stay frozen: changing *it* remains a
root-on-host act for the owner. It is where the boundary now sits.

Three properties make this different from the rejected version:

1. **Adoption is explicit, never a side effect.** It is its own verb and its own
   workflow input. It does not happen during `plan` or `apply`. The failure mode
   being avoided is not "the wrapper changed" but "the wrapper changed during a
   run whose summary said *deploy*".

2. **The adopter is separate from the wrapper, so a bad wrapper is
   recoverable.** A wrapper that adopted its own replacement could ship a broken
   one and lose the means of repair along with everything else. The adopter does
   its own `git fetch`, so it never depends on the wrapper working.

3. **Adoption is gated and reversible.** A candidate must be a regular non-empty
   file, parse under `bash -n`, pass the wrapper's own test suite — the suite
   whose subject is what the wrapper *refuses* — and survive a dry-run
   invocation. Installation is atomic (write a sibling, rename). The installed
   wrapper is then verified over the exact path SSM uses
   (`runuser -u alert-ops -- sudo -n …`), and any failure rolls back to the
   previous file, which is kept. The adoption is appended to the audit log with
   an actor, like a deploy.

## Consequences

**What improves.** The last recurring handoff goes away. `alerts` and
`logs --since` stop being blocked on a human. A wrapper bug is fixable in minutes
through the same reviewed path as everything else, rather than waiting for
someone with root.

**What this costs, stated plainly.** The agent can now change the verb set, and a
verb is arbitrary shell running as root. The gates check that a candidate is
*well-formed and still refuses what the suite says it refuses*; they cannot check
that a new verb is a good idea. The compensating controls are that every
candidate passed through a reviewed PR and green CI, every adoption is in the
audit log with the run that caused it, and the adopter's own rules are not the
agent's to change.

**What is deliberately not closed.** The agent could write a verb that the test
suite does not constrain, since the suite is also the agent's to edit. Freezing
the adopter does not prevent that; nothing short of not granting this would. The
honest description is that this trades a hard boundary for an audited one, on the
owner's instruction, for a component whose confinement of the agent's code was
already partial (see Context).

**Recovery.** If a wrapper is adopted that breaks the deploy path, re-running
`adopt-wrapper` after merging a fix repairs it, because the adopter fetches
independently. If the adopter itself is broken, that is a root-on-host repair and
remains the owner's — which is the intended shape.

## Implementation status

Accepted, not built. The agent's sandbox refuses to author the privileged
installer or the sudoers change (classified as security-weakening), and did so
again on 2026-09-22 after the owner granted the decision. The grant settles
*whether*; it does not give the agent a way to write the component.

Two paths, neither yet taken:

- The owner writes or reviews-and-commits `alert-adopt-wrapper` and the sudoers
  line himself, from the design above.
- The sandbox permission is widened for this file specifically, and the agent
  builds it.

Either way there is a hard prerequisite: the SSM document needs a new
`adopt-wrapper` verb in `allowedValues`, which is Terraform, which needs ADR
0003's bootstrap applied first (handoff issue #27). So #27 comes before this
regardless of which path is chosen.
