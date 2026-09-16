"""The skills the environment must actually have, and how that is established.

A skill written into an image is not a skill an agent has. Two things can go
wrong quietly: the install fetches whatever is current rather than what was
pinned, and the files land somewhere the agent never looks. So this asks the
environment itself — for each required skill, where its `SKILL.md` is and what
its digest is — and compares that against a pin.

The pins for Orca's own skills come from Orca's bundled manifest, which records
a digest per file for the version installed. A skill whose digest does not
match was fetched from somewhere else, or drifted, and the run is blocked
rather than continued on the assumption that a name is enough.

A plugin is pinned by its commit instead of by one file, because it is a
repository. The check is the commit the environment actually has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .result import PhaseStatus, SkillsRecord

#: Printed by the probe between the fields of one answer.
SEPARATOR = "\t"

MISSING = "missing"

LOCATE_SCRIPT = r"""
set -u
roots="$1"
shift
for name in "$@"; do
    found=""
    for root in $roots; do
        candidate="$HOME/$root/$name/SKILL.md"
        if [ -f "$candidate" ]; then
            found="$candidate"
            break
        fi
    done
    if [ -z "$found" ]; then
        printf '%s\tmissing\t\n' "$name"
    else
        printf '%s\t%s\t%s\n' "$name" "$found" "$(sha256sum "$found" | cut -d' ' -f1)"
    fi
done
"""

REVISION_SCRIPT = r"""
set -u
for entry in "$@"; do
    name="${entry%%=*}"
    path="${entry#*=}"
    if [ ! -d "$path/.git" ]; then
        printf '%s\tmissing\t%s\n' "$name" "$path"
    else
        printf '%s\t%s\t%s\n' "$name" "$(git -C "$path" rev-parse HEAD 2>/dev/null)" "$path"
    fi
done
"""


def locate_argv(roots: Sequence[str], names: Sequence[str]) -> list[str]:
    """Return one command that reports where each skill is and what it hashes to."""
    return ["sh", "-c", LOCATE_SCRIPT, "cycle-skills", " ".join(roots), *names]


def revision_argv(pairs: Sequence[tuple[str, str]]) -> list[str]:
    """Return one command that reports the commit each plugin checkout is at."""
    return [
        "sh",
        "-c",
        REVISION_SCRIPT,
        "cycle-plugins",
        *[f"{name}={path}" for name, path in pairs],
    ]


def parse(stdout: str) -> dict[str, tuple[str, str]]:
    """Return {name: (second field, third field)} for each answered line."""
    answers: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        parts = line.rstrip("\n").split(SEPARATOR)
        if len(parts) >= 3 and parts[0]:
            answers[parts[0]] = (parts[1], parts[2])
    return answers


@dataclass
class Finding:
    """One required skill, and whether the environment really has that one."""

    name: str
    kind: str
    expected: str
    observed: str = ""
    where: str = ""
    ok: bool = False
    detail: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "expected": self.expected,
            "observed": self.observed,
            "where": self.where,
            "ok": self.ok,
            "detail": self.detail,
        }


def _judge_bundled(name, expected, answer) -> Finding:
    finding = Finding(name=name, kind="skill", expected=expected)
    if answer is None:
        finding.detail = "the environment did not answer for this skill"
        return finding
    where, digest = answer
    finding.where = "" if where == MISSING else where
    finding.observed = digest
    if where == MISSING:
        finding.detail = "no SKILL.md for this skill anywhere the agent looks"
        return finding
    if not expected:
        finding.detail = "this skill is required but nothing pins it"
        return finding
    if digest != expected:
        finding.detail = (
            "the skill at this path is not the pinned one; it was fetched from "
            "somewhere else or has drifted"
        )
        return finding
    finding.ok = True
    finding.detail = "present, and the same bytes the pin names"
    return finding


def _judge_plugin(name, expected, answer) -> Finding:
    finding = Finding(name=name, kind="plugin", expected=expected)
    if answer is None:
        finding.detail = "the environment did not answer for this plugin"
        return finding
    revision, path = answer
    finding.where = path
    finding.observed = "" if revision == MISSING else revision
    if revision == MISSING:
        finding.detail = "there is no checkout at this path"
        return finding
    if not expected:
        finding.detail = "this plugin is required but nothing pins it"
        return finding
    if revision != expected:
        finding.detail = "the checkout is at a different commit than the pin"
        return finding
    finding.ok = True
    finding.detail = "checked out at the pinned commit"
    return finding


def judge(
    *,
    bundled: Sequence[tuple[str, str]],
    plugins: Sequence[tuple[str, str]],
    skill_answers: dict[str, tuple[str, str]],
    plugin_answers: dict[str, tuple[str, str]],
) -> tuple[list[Finding], bool]:
    """Return one finding per requirement, and whether every one of them holds."""
    findings = [
        _judge_bundled(name, expected, skill_answers.get(name))
        for name, expected in bundled
    ]
    findings.extend(
        _judge_plugin(name, expected, plugin_answers.get(name))
        for name, expected in plugins
    )
    return findings, all(finding.ok for finding in findings)


def record(findings: Sequence[Finding], required: bool) -> SkillsRecord:
    """Turn findings into the record the manifest carries."""
    if not findings:
        return SkillsRecord(
            status=PhaseStatus.SKIPPED,
            detail="this configuration requires no skills in the environment",
        )
    failed = [finding for finding in findings if not finding.ok]
    if not failed:
        return SkillsRecord(
            status=PhaseStatus.OK,
            findings=[finding.to_document() for finding in findings],
            detail=(
                f"all {len(findings)} required skill(s) are present in the "
                "environment at the pinned revision"
            ),
        )
    detail = "; ".join(f"{finding.name}: {finding.detail}" for finding in failed)
    return SkillsRecord(
        status=PhaseStatus.BLOCKED if required else PhaseStatus.FAILED,
        findings=[finding.to_document() for finding in findings],
        detail=f"the environment does not carry what was pinned — {detail}",
    )
