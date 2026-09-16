"""Handing the implementer's work to a reviewer that did not do it.

Two agents in two panes are not two agents. What makes a review independent is
that the reviewer is a separate dispatch, in its own session, with its own
context, which was handed an artifact rather than a claim — and that the
artifact is demonstrably the one the implementer produced.

So Control does the handoff itself. After the implementer settles it captures
the diff inside the environment, takes its digest, and gives the reviewer the
path and nothing else. When the review returns, the digest is taken again: a
diff that changed underneath the review is a review of something else. The
reviewer is also asked to report the digest it read, so its verdict can be tied
to the same bytes.

What this establishes is that the reviewer was handed the implementer's diff
and answered about it. It does not establish that the reviewer read every line;
nothing here can.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

#: Written by Control inside the environment, read by the reviewer.
DIFF_NAME = "handoff-diff.patch"
VERDICT_NAME = "review-verdict.json"
PROMPT_NAME = "review-prompt.md"

#: Stages everything, including files the implementer created, and writes the
#: patch out with its digest. `git diff` alone omits an untracked new file,
#: which is exactly what this task produces.
CAPTURE_SCRIPT = r"""
set -eu
project="$1"
target="$2"
git -C "$project" add -A
git -C "$project" diff --cached > "$target"
printf 'sha256 %s\n' "$(sha256sum "$target" | cut -d' ' -f1)"
printf 'lines %s\n' "$(wc -l < "$target")"
printf 'bytes %s\n' "$(wc -c < "$target")"
"""

DIGEST_SCRIPT = r"""
set -eu
target="$1"
if [ ! -f "$target" ]; then
    printf 'sha256 missing\n'
    exit 0
fi
printf 'sha256 %s\n' "$(sha256sum "$target" | cut -d' ' -f1)"
"""

READ_SCRIPT = r"""
set -eu
target="$1"
if [ ! -f "$target" ]; then
    printf '{}\n'
    exit 0
fi
cat "$target"
"""

PROMPT_TEMPLATE = """# Review this change

You are the reviewer. You did not write this change and you are not being asked
to fix it.

The implementer's diff is at `{diff_path}`. The project it applies to is at
`{project_path}`. Read the diff.

The change was supposed to do exactly one thing: create `{relative_path}` in the
project containing exactly `{marker}`, with no trailing newline and nothing
else.

When you have read it, write `{verdict_path}` as a JSON object with exactly
these keys, and nothing else:

    {{
      "diff_sha256": "<the sha256 of the diff file you read>",
      "verdict": "<accept or reject>",
      "reason": "<one sentence>"
    }}

Get the digest with `sha256sum {diff_path}`.

Do not change the diff, the project, or anything else. When the verdict file
exists with exactly those three keys, report that you are done.
"""


def capture_argv(project_path: str, diff_path: str) -> list[str]:
    """Return the command that writes the implementer's diff and measures it."""
    return ["sh", "-c", CAPTURE_SCRIPT, "cycle-handoff", project_path, diff_path]


def digest_argv(diff_path: str) -> list[str]:
    """Return the command that re-reads the diff's digest."""
    return ["sh", "-c", DIGEST_SCRIPT, "cycle-handoff", diff_path]


def read_argv(verdict_path: str) -> list[str]:
    """Return the command that brings the reviewer's verdict back."""
    return ["sh", "-c", READ_SCRIPT, "cycle-handoff", verdict_path]


def parse_capture(stdout: str) -> dict[str, Any]:
    """Return {sha256, lines, bytes} from the capture command's output."""
    found: dict[str, Any] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        key, value = parts
        if key == "sha256":
            found["sha256"] = value
        elif key in ("lines", "bytes"):
            try:
                found[key] = int(value)
            except ValueError:
                pass
    return found


def parse_verdict(stdout: str) -> dict[str, Any]:
    """Return the reviewer's verdict document, or an empty one."""
    stripped = stdout.strip()
    if not stripped:
        return {}
    for candidate in (stripped, *stripped.splitlines()):
        text = candidate.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def build_prompt(
    *, diff_path: str, project_path: str, verdict_path: str, relative_path: str, marker: str
) -> str:
    """Render the reviewer's instructions."""
    return PROMPT_TEMPLATE.format(
        diff_path=diff_path,
        project_path=project_path,
        verdict_path=verdict_path,
        relative_path=relative_path,
        marker=marker,
    )


@dataclass(frozen=True)
class Separation:
    """Whether the two dispatches really were two."""

    separate_dispatch: bool
    separate_terminal: bool
    same_run: bool
    detail: str

    @property
    def ok(self) -> bool:
        return self.separate_dispatch and self.separate_terminal and self.same_run

    def to_document(self) -> dict[str, Any]:
        return {
            "separate_dispatch": self.separate_dispatch,
            "separate_terminal": self.separate_terminal,
            "same_run": self.same_run,
            "ok": self.ok,
            "detail": self.detail,
        }


def separation(implementer, reviewer) -> Separation:
    """Compare the two dispatches by what the plane named them, not by layout.

    A panel, a focus and a position prove nothing about whose session is whose.
    The identifiers do: two dispatches, two agent terminals, and one Run that
    both belong to, which is what makes the second one a handoff.
    """
    missing = [
        name
        for name, value in (
            ("the implementer's dispatch", implementer.dispatch_id),
            ("the reviewer's dispatch", reviewer.dispatch_id),
            ("the implementer's terminal", implementer.terminal),
            ("the reviewer's terminal", reviewer.terminal),
        )
        if not value
    ]
    if missing:
        return Separation(
            separate_dispatch=False,
            separate_terminal=False,
            same_run=False,
            detail="the plane never named " + ", ".join(missing),
        )
    separate_dispatch = implementer.dispatch_id != reviewer.dispatch_id
    separate_terminal = implementer.terminal != reviewer.terminal
    same_run = bool(implementer.run_id) and implementer.run_id == reviewer.run_id
    if separate_dispatch and separate_terminal and same_run:
        detail = (
            "two dispatches with two agent terminals, both under the Run the "
            "implementer's dispatch created"
        )
    else:
        problems = []
        if not separate_dispatch:
            problems.append("both dispatches carry the same identifier")
        if not separate_terminal:
            problems.append("both agents were given the same terminal")
        if not same_run:
            problems.append("the review does not belong to the implementer's Run")
        detail = "; ".join(problems)
    return Separation(
        separate_dispatch=separate_dispatch,
        separate_terminal=separate_terminal,
        same_run=same_run,
        detail=detail,
    )


def judge_same_diff(
    *, captured: str, after: str, reported: str
) -> tuple[bool, str]:
    """Say whether the reviewer answered about the diff it was handed."""
    if not captured:
        return False, "no diff was captured, so there is nothing a review was of"
    if after == "missing":
        return False, "the diff was gone by the time the review finished"
    if after != captured:
        return False, (
            "the diff changed while the review was running, so the verdict is "
            "about something other than what was handed over"
        )
    if not reported:
        return False, (
            "the reviewer did not report which diff it read, so its verdict "
            "cannot be tied to this one"
        )
    if reported != captured:
        return False, (
            "the reviewer reported reading a different diff than the one it was "
            "handed"
        )
    return True, (
        "the diff handed over, the diff still on disk and the diff the reviewer "
        "reported reading are the same bytes"
    )
