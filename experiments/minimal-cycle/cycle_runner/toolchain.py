"""Which toolchain the environment actually runs, as opposed to which one it installed.

A provisioning step that unpacks a pinned node and installs a pinned Claude
Code establishes what landed on the disk. It does not establish what runs.
Those are different claims, and on the container backend they came apart: the
box inherits the host's search path, that path opens with directories under
the operator's mounted home, and so every bare program name inside the box
answered with the operator's own build. The provisioning steps printed the
proof of it — a step that unpacked v24.21.0 ended by printing v24.19.0 — and a
printed version nobody compares is not a check.

`isolate` fixes the ordering. This module is what says whether the fix reached
the thing that matters, which is not the runner's own shell but the agent. The
agent is not launched by this runner at all: the runner asks the execution
plane, the plane's application spawns the process, and whatever search path
that application holds is what decides which Claude Code the agent is. So two
questions are asked, and neither is asked of the runner's own shell:

*What does a bare name select in the environment now, and what else could it
have selected?* Naming the shadow that lost is the point: a resolution report
that lists one path proves nothing, because it reads the same whether the
ordering was fixed or there was never a second candidate.

*What is the agent's own process executing?* This is the direct answer, read
from the process itself rather than inferred from a path. It is restricted to
processes whose home is this run's per-run home, which is what keeps a survey
run inside a box that shares the host's process table off the operator's own
Claude Code — the same rule `processes` follows, for the same reason.

Neither question is always answerable. A process that has already exited
cannot be read, and this says so rather than reporting a clean result: an
unanswered question is recorded as unanswered, never as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .status import PhaseStatus

#: The program the execution plane launches an agent as. Named here because
#: the runner never types it: the plane does, inside the environment.
AGENT_PROGRAM = "claude"

#: What the environment is asked, and how it answers. Both scripts are POSIX
#: shell and read only what any image exposes, because they run unchanged on
#: the container backend and in the machine backend's guest.
RESOLUTION_SCRIPT = r"""
set -u
program="${1:-}"
selected="$(command -v "$program" 2>/dev/null || printf '')"
printf 'selected=%s\n' "$selected"
if [ -n "$selected" ]; then
    printf 'selected_version=%s\n' \
        "$("$selected" --version 2>/dev/null | head -n 1 | cut -d ' ' -f 1 || printf '')"
fi
# Every other one the same search path can reach, in the order it would try
# them. A report naming only the winner cannot tell a fixed ordering from an
# environment that never had a second candidate.
saved_ifs="$IFS"
IFS=:
for directory in $PATH; do
    IFS="$saved_ifs"
    [ -n "$directory" ] || continue
    [ -x "$directory/$program" ] || continue
    printf 'reachable=%s\n' "$directory/$program"
    IFS=:
done
IFS="$saved_ifs"
"""

PROCESS_SCRIPT = r"""
set -u
home="${1:-}"
examined=0
for entry in /proc/[0-9]*; do
    [ -r "$entry/environ" ] || continue
    process_home="$(tr '\0' '\n' < "$entry/environ" 2>/dev/null \
        | sed -n 's/^HOME=//p' | head -n 1)"
    # The container backend's box shares the host's process table, so this is
    # what keeps the survey off the operator's own processes rather than a
    # program name, which would reach exactly them.
    [ "$process_home" = "$home" ] || continue
    binary="$(readlink "$entry/exe" 2>/dev/null || printf '')"
    [ -n "$binary" ] || continue
    program="$(tr '\0' '\n' < "$entry/cmdline" 2>/dev/null | head -n 1)"
    printf 'process=%s\t%s\n' "$binary" "$program"
    examined=$((examined + 1))
done
printf 'examined=%s\n' "$examined"
"""


def resolution_argv(program: str) -> list[str]:
    """Return the argv that asks the environment what a bare name selects."""
    return ["sh", "-c", RESOLUTION_SCRIPT, "cycle-toolchain", program]


def process_argv(home_path: str) -> list[str]:
    """Return the argv that asks the environment what this run's processes run."""
    return ["sh", "-c", PROCESS_SCRIPT, "cycle-toolchain", home_path]


def parse_resolution(text: str) -> dict[str, Any]:
    """Parse the resolution probe's output; unrecognised lines are dropped."""
    selected = ""
    version = ""
    reachable: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if key == "selected":
            selected = value
        elif key == "selected_version":
            version = value
        elif key == "reachable" and value:
            # A real search path repeats itself — this host's names
            # ~/.local/bin six times — and the same binary listed six times
            # reads as six candidates. Order is kept because it is the whole
            # meaning of the list: the first entry is what a bare name runs.
            if value not in reachable:
                reachable.append(value)
    return {"selected": selected, "selected_version": version, "reachable": tuple(reachable)}


def parse_processes(text: str) -> tuple[tuple[str, str], ...]:
    """Return each of this run's processes as its executable and its program."""
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key != "process":
            continue
        binary, _, program = value.partition("\t")
        binary = binary.strip()
        if binary:
            found.append((binary, program.strip()))
    return tuple(found)


def running(processes: Sequence[tuple[str, str]], program: str) -> tuple[str, ...]:
    """Return the distinct executables of this run's processes that run `program`.

    Matching on the name is safe here and only here: the caller has already
    restricted the set to processes whose home is this run's own, so the name
    describes what was found rather than deciding what to reach for.
    """
    named = {
        binary
        for binary, command in processes
        if program in binary or program in command
    }
    return tuple(sorted(named))


@dataclass
class ToolchainRecord:
    """What the environment resolves, what it ran, and whether that is the pin."""

    program: str
    pinned: str
    #: Whether anything here answered the question at all. A run that could not
    #: read the agent's process has not established that the agent was right.
    established: bool = False
    selected: str = ""
    selected_version: str = ""
    reachable: tuple[str, ...] = ()
    #: The reachable builds that are not the environment's own, in the order
    #: the search path would try them. The first of these is what the bare name
    #: resolved to before the ordering was fixed, so a record carrying both
    #: this and `selected` says what changed without needing a second run.
    shadowed: tuple[str, ...] = ()
    agent_binaries: tuple[str, ...] = ()
    status: PhaseStatus = PhaseStatus.SKIPPED
    detail: str = "not asked"
    commands: list = field(default_factory=list)

    def to_document(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "pinned": self.pinned,
            "established": self.established,
            "selected": self.selected,
            "selected_version": self.selected_version,
            "reachable": list(self.reachable),
            "shadowed": list(self.shadowed),
            "agent_binaries": list(self.agent_binaries),
            "status": self.status.value,
            "detail": self.detail,
        }


def judge(
    *,
    program: str,
    pinned: str,
    resolution: Mapping[str, Any],
    agent_binaries: Sequence[str],
    own_prefix: str = "/usr/local/",
) -> ToolchainRecord:
    """Decide whether the environment's own installation is what a bare name runs.

    What this gates on is the one question that can be answered exactly: the
    binary a bare name selects is asked for its version, and that version is
    *compared* to the pin. A version that is printed and not compared reads the
    same whatever is installed, which is the defect this exists to catch, so
    printing it is not enough and finding a path is not either.

    What the agent's own process is running is carried alongside and does not
    gate. It is the better evidence and the less certain reading: a process
    that has already exited cannot be read at all, and a Claude Code that
    re-executes through an interpreter names the interpreter rather than
    itself. A gate built on guessing which of those happened would fail runs
    over its own guess. So the process is reported as what it is, and the
    record says plainly whether it settled the question or left it open.
    """
    record = ToolchainRecord(
        program=program,
        pinned=pinned,
        selected=str(resolution.get("selected") or ""),
        selected_version=str(resolution.get("selected_version") or ""),
        reachable=tuple(resolution.get("reachable") or ()),
        agent_binaries=tuple(agent_binaries),
    )
    record.shadowed = tuple(
        path
        for path in record.reachable
        if not path.startswith(own_prefix) and path != record.selected
    )
    if not record.selected:
        record.status = PhaseStatus.BLOCKED
        record.detail = (
            f"nothing in the environment answers to {program!r}, so the agent has "
            "nothing of its own to run"
        )
        return record
    if record.selected_version != pinned:
        record.status = PhaseStatus.BLOCKED
        record.detail = (
            f"{program} resolves to {record.selected}, which reports "
            f"{record.selected_version or 'no version'} rather than the pinned "
            f"{pinned}"
        )
        return record

    record.status = PhaseStatus.OK
    # The ordering is now right. Whether that reached the agent is a separate
    # claim, and it is only established when a process of this run was still
    # there to be read.
    record.established = bool(agent_binaries)
    ran = (
        f"this run's own process is running {', '.join(record.agent_binaries)}"
        if agent_binaries
        else (
            "no process of this run was still running it, so what the agent ran is "
            "not established here"
        )
    )
    record.detail = (
        f"{program} resolves to {record.selected} at the pinned {pinned}, "
        f"{len(record.shadowed)} other build(s) stayed reachable behind it, and {ran}"
    )
    return record
