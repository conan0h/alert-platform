#!/usr/bin/env python3
"""Refuse a wildcard inside an IAM Deny action list.

An explicit Deny beats every Allow, so a wildcard in a Deny is not a
convenience — it is a claim about every current and future action name that
happens to match. On 2026-09-22 that claim was wrong in a way that took the
whole control plane out:

    actions = ["iam:*OpenIDConnectProvider*"]

was meant to say "the agent may not repoint its own trust anchor". It also
matched `iam:GetOpenIDConnectProvider` and `iam:ListOpenIDConnectProviders`,
which overrode the `iam:Get*` Allow the plan depends on. Terraform refreshes
every resource in state before it plans anything, so every `infra.yml` plan
failed with AccessDenied before printing a single change — the role could not
read the resource it was forbidden to change, and so could do nothing at all.

The rule this enforces: a Deny enumerates its actions. Writing them out forces
the author to decide, for each one, whether it mutates. An Allow may still use
wildcards — over-granting there is caught by the boundary and by review, and
`ec2:Describe*` is genuinely what a plan needs.

Run: python3 tools/check_iam_denies.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEARCH = [ROOT / "infra"]

# Statements are split on the `statement {` keyword rather than matched from
# `effect`, so `sid` is in scope wherever the author put it — naming the
# offending statement is most of what makes the failure actionable.
STATEMENT_SPLIT = re.compile(r"\bstatement\s*\{")
IS_DENY = re.compile(r'effect\s*=\s*"Deny"')
ACTION_LIST = re.compile(r"actions\s*=\s*(?P<list>\[.*?\])", re.DOTALL)
QUOTED = re.compile(r'"([^"]+)"')
SID = re.compile(r'sid\s*=\s*"([^"]+)"')


def find_tf_files() -> list[Path]:
    files: list[Path] = []
    for base in SEARCH:
        if base.is_dir():
            files.extend(sorted(base.rglob("*.tf")))
    return files


def deny_statements(text: str):
    """Yield (offset, body) for each Deny statement block."""
    bounds = [m.end() for m in STATEMENT_SPLIT.finditer(text)]
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        body = text[start:end]
        if IS_DENY.search(body):
            yield start, body


def check(path: Path) -> list[str]:
    """Return one message per offending action."""
    text = path.read_text()
    problems: list[str] = []

    for start, body in deny_statements(text):
        actions = ACTION_LIST.search(body)
        if not actions:
            continue

        # Strip comments so a wildcard quoted in prose is not a finding. This
        # file's own explanation names the pattern it forbids.
        listing = "\n".join(
            line.split("#", 1)[0] for line in actions.group("list").splitlines()
        )

        sid_match = SID.search(body)
        sid = sid_match.group(1) if sid_match else "(no sid)"
        line_no = text[:start].count("\n") + 1

        for action in QUOTED.findall(listing):
            if "*" in action:
                problems.append(
                    f"{path.relative_to(ROOT)}:{line_no}: Deny statement "
                    f"{sid!r} uses the wildcard action {action!r}. Enumerate "
                    f"the mutating actions instead: a wildcard Deny also "
                    f"matches Get*/List*/Describe* and overrides the Allow a "
                    f"plan needs."
                )
    return problems


def main() -> int:
    files = find_tf_files()
    if not files:
        print("check_iam_denies: no .tf files found under infra/", file=sys.stderr)
        return 1

    problems: list[str] = []
    deny_count = 0
    for path in files:
        deny_count += sum(1 for _ in deny_statements(path.read_text()))
        problems.extend(check(path))

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print(f"\n{len(problems)} wildcard action(s) in Deny statements.", file=sys.stderr)
        return 1

    # Report the count so a regex that silently stops matching is visible. Zero
    # Deny statements would mean this check passes while guarding nothing.
    print(f"OK: {deny_count} Deny statement(s) across {len(files)} file(s), "
          f"no wildcard actions.")
    if deny_count == 0:
        print("check_iam_denies: found no Deny statements at all, which is "
              "almost certainly a broken pattern rather than a clean repo.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
