"""What the run started, and whether any of it is still on the host.

A backend that shares namespaces with its host — Distrobox shares the process
table, the message bus and the display sockets — can leave a process running
after the resource that nominally contained it is gone. Removing the container
reports success, and the application it held keeps running, keeps its window on
the host's screen, and keeps writing to a directory the run has deleted.

So a run surveys for processes still holding a path of its own, stops them, and
reports what it stopped. The survey is by path, never by program name: a name
would reach the operator's own application, which is the failure this exists to
prevent rather than a worse version of it.

Three things follow from that rule and are the whole of this module.

**A process is signalled only when something readable ties it to this run.**
The evidence is its own `HOME`, its own working directory, its own command
line, or the mount namespace another process already tied to a path
established. Nothing is inferred from a program name, and nothing is inferred
from a process merely being near one of this run's.

**A process this survey cannot read is not a process it may skip.** The
operator's own processes are readable here, and the ones that are not are
running as somebody else — which on this host means a box's own `sudo`,
mapped to a subordinate identifier the operator cannot look into. Such a
process cannot be shown to be this run's, and it cannot be shown not to be.
Calling that "no match" is how a survey reports a clean host over a process it
never managed to look at. It is named instead, and the caller refuses over it.

**A signal that did not reach anything is not a stop.** `kill` fails with
`EPERM` for exactly the processes described above, and a loop that swallows the
error counts them as stopped.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

PROC_ROOT = Path("/proc")


@dataclass(frozen=True)
class Process:
    """One process, and everything that can tie it to this run.

    The command line alone is not enough. The application this runner starts
    appears in the process table as `orca-ide --disable-gpu` and names no path
    at all; only its children carry `--user-data-dir`, and only its own
    environment and working directory name the home it was given. A survey that
    reads argv finds the children and leaves the parent running.
    """

    pid: int
    command: str
    parent: int = 0
    home: str = ""
    cwd: str = ""
    #: The session variables in this process's environment, with their values.
    #: What matters is where a value points, not that the name is present.
    session: dict[str, str] = field(default_factory=dict)
    #: This process's mount namespace, as `/proc/<pid>/ns/mnt` names it. It is
    #: what tells a process running inside the environment from the host-side
    #: plumbing that launched it — and that plumbing matches this run's paths
    #: too, because the run's own paths are on its command line.
    namespace: str = ""
    #: What this host would not let the survey read about this process, named
    #: one by one. Empty is the ordinary case and means every question was put
    #: and answered; anything here means a question was refused, which is not
    #: the same as an answer of no.
    unreadable: tuple[str, ...] = ()

    def inside(self, host_namespace: str) -> bool:
        """Report whether this process runs in an environment of its own."""
        return bool(self.namespace) and bool(host_namespace) and self.namespace != host_namespace

    def evidence(self, path: str) -> tuple[str, ...]:
        """Return this process's own fields in which the given path appears.

        One matcher, so there is one place a wrong rule could be written. The
        order is the order of strength: a home and a working directory are
        where the process *is*, and a command line is what somebody wrote about
        it — the host-side plumbing that drives the environment carries this
        run's paths on its argv while belonging to the host.
        """
        return tuple(
            name
            for name, value in (
                ("home", self.home),
                ("working directory", self.cwd),
                ("command line", self.command),
            )
            if path in value
        )

    def names(self, path: str) -> bool:
        """Report whether this process is tied to the given path."""
        return bool(self.evidence(path))


#: The fields that say where a process *is*, rather than what it was told. Only
#: these may establish that a mount namespace is this run's environment.
#:
#: Measured, not assumed: on this host a rootless container manager runs its
#: own client and its monitor in a shared namespace of their own — one
#: namespace for every box that user has, the operator's included. Those
#: processes carry this run's paths on their command lines, because the paths
#: are what they were asked to mount. Seeding from a command line would
#: therefore have taken every container on the host as this run's.
LOCATING_FIELDS = ("home", "working directory")


@dataclass(frozen=True)
class Attribution:
    """One process, and what does or does not tie it to one run."""

    process: Process
    #: Why this process is this run's. Empty means it was never established.
    reasons: tuple[str, ...] = ()
    #: Why this process is this run's business at all, when nothing readable
    #: established that it is this run's. Empty means it is simply not this
    #: run's, which is the ordinary answer for almost every process on a host.
    implicated: str = ""

    @property
    def pid(self) -> int:
        return self.process.pid

    def describe(self) -> str:
        """This process and what decided it, in one line an operator reads."""
        said = f"pid {self.pid} {self.process.command[:80]}"
        why = "; ".join(self.reasons) if self.reasons else self.implicated
        return f"{said} ({why})" if why else said


@dataclass(frozen=True)
class Ownership:
    """Which processes on this host are one run's, and which cannot be said."""

    own: tuple[Attribution, ...] = ()
    unattributed: tuple[Attribution, ...] = ()

    @property
    def pids(self) -> list[int]:
        return [item.pid for item in self.own]

    @property
    def settled(self) -> bool:
        """Whether every process near this run was decided one way or the other."""
        return not self.unattributed


@dataclass
class SurveyReport:
    """What was still running, what was stopped, and what would not stop."""

    found: list[int] = field(default_factory=list)
    stopped: list[int] = field(default_factory=list)
    surviving: list[int] = field(default_factory=list)
    #: Processes this run could neither claim nor rule out. Nothing is
    #: signalled while any of these stand, so this is a refusal and not a list
    #: of casualties.
    unattributed: list[int] = field(default_factory=list)
    #: Why each found process is this run's, by pid.
    attribution: dict[int, str] = field(default_factory=dict)
    #: Why each unattributed process could not be decided, by pid.
    unreadable: dict[int, str] = field(default_factory=dict)
    #: Why a signal did not reach a process, by pid. `EPERM` lands here, and a
    #: process that is here is a process that was not stopped.
    refused: dict[int, str] = field(default_factory=dict)
    detail: str = "no survey ran"

    @property
    def clean(self) -> bool:
        return not self.surviving and not self.unattributed

    def to_document(self) -> dict[str, Any]:
        return {
            "found": list(self.found),
            "stopped": list(self.stopped),
            "surviving": list(self.surviving),
            "unattributed": list(self.unattributed),
            "attribution": {str(pid): why for pid, why in self.attribution.items()},
            "unreadable": {str(pid): why for pid, why in self.unreadable.items()},
            "refused": {str(pid): why for pid, why in self.refused.items()},
            "clean": self.clean,
            "detail": self.detail,
        }


def _read(pid: int, name: str, denied: list[str] | None = None, label: str = "") -> str:
    """Read one `/proc` file, telling a refusal apart from an empty answer.

    `PermissionError` is the whole point of the distinction: it is what this
    host answers for a process running as somebody else, and it is the one
    outcome that means the question was not put rather than answered.
    """
    try:
        raw = (PROC_ROOT / str(pid) / name).read_bytes()
    except PermissionError:
        if denied is not None:
            denied.append(label or name)
        return ""
    except (OSError, ValueError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


#: Variables that can point at the operator's screen, keys or message bus.
SESSION_NAMES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


def _read_environment(
    pid: int, denied: list[str] | None = None
) -> tuple[str, dict[str, str]]:
    """Return this process's home, and the session variables it carries.

    The values are kept because presence alone decides nothing: a message bus
    address under the environment's own runtime directory is the environment's
    own bus, and a variable of the same name pointing at the operator's is the
    leak. Nothing exports these values; only what is concluded from them.
    """
    home = ""
    carried: dict[str, str] = {}
    for pair in _read(pid, "environ", denied, "its environment").split(" "):
        if pair.startswith("HOME="):
            home = pair[5:]
            continue
        name, _, value = pair.partition("=")
        if name in SESSION_NAMES:
            carried[name] = value
    return home, carried


def _readlink(pid: int, name: str, denied: list[str], label: str) -> str:
    """Follow one `/proc` link, telling a refusal apart from an absent one."""
    try:
        return os.readlink(PROC_ROOT / str(pid) / name)
    except PermissionError:
        denied.append(label)
        return ""
    except OSError:
        return ""


def mount_namespace(pid: int | str = "self") -> str:
    """Return the mount namespace of a process, or the empty string."""
    try:
        return os.readlink(PROC_ROOT / str(pid) / "ns" / "mnt")
    except OSError:
        return ""


def _read_parent(pid: int) -> int:
    for line in _read(pid, "status").splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


def attribute(
    paths: Sequence[str],
    *,
    lister: Callable[[], Iterable[Process]] | None = None,
    exclude: Sequence[int] = (),
    roots: Sequence[int] = (),
    host_namespace: str | None = None,
) -> Ownership:
    """Decide, for every process on this host, whether it belongs to one run.

    A root is a process identifier the run recorded when it started something.
    It is only meaningful on a backend that shares the host's process table,
    so the caller decides whether to pass one: on a backend that does not, the
    same number belongs to some unrelated process on this machine.

    What comes back is two lists, and the second is the reason this function
    exists. A process whose own fields this host would not let the survey read
    cannot be claimed — and a survey that quietly drops it reports a clean host
    over a process it never looked at. Such a process is named here when
    something else puts it inside this run: its parent is one of this run's.
    Nothing further is inferred from that, and in particular it is not taken as
    this run's on its parent's word.

    The limit of that, said plainly: a container that shares the host's process
    table gives an orphaned process inside it a parent outside the run, so an
    unreadable process whose parent has already exited is not seen here at all.
    Removing the container is what reaps that one, which is why the command
    that removes the container is the one that calls this.
    """
    wanted = [str(path) for path in paths if str(path).strip()]
    skip = set(exclude) | {os.getpid(), os.getppid()}
    table = [
        process
        for process in (list(lister()) if lister is not None else _live_processes())
        if process.pid not in skip
    ]
    by_pid = {process.pid: process for process in table}
    reasons: dict[int, list[str]] = {}
    located: set[int] = set()

    for process in table:
        for path in wanted:
            for name in process.evidence(path):
                reasons.setdefault(process.pid, []).append(f"its {name} names {path}")
                if name in LOCATING_FIELDS:
                    located.add(process.pid)

    # The environment's own mount namespace, learned from a process that a
    # path of this run says is *in* it. Everything else in that namespace is
    # in the same environment, including what carries no path of its own.
    host = mount_namespace() if host_namespace is None else host_namespace
    namespaces = {
        process.namespace
        for process in table
        if process.pid in located and process.inside(host)
    }
    for process in table:
        if process.pid in reasons or not process.namespace:
            continue
        if process.namespace in namespaces:
            reasons[process.pid] = [
                f"it runs in the mount namespace {process.namespace}, which a "
                "process this run's own home or working directory named "
                "established as this run's environment"
            ]

    for root in roots:
        if root in skip or root <= 1:
            continue
        if root in by_pid:
            reasons.setdefault(by_pid[root].pid, []).append(
                f"this run recorded it as pid {root} when it started it"
            )
        for process in _tree(table, root):
            reasons.setdefault(process.pid, []).append(
                f"it descends from pid {root}, which this run started"
            )

    children: dict[int, list[Process]] = {}
    for process in table:
        children.setdefault(process.parent, []).append(process)

    # A process of this run's whose own fields cannot be read. The walk goes on
    # through such a process, because what it started cannot be read either.
    opaque: dict[int, str] = {}
    pending = list(reasons)
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            if child.pid in reasons or child.pid in opaque or not child.unreadable:
                continue
            opaque[child.pid] = (
                f"its parent pid {parent} is this run's, and this host would "
                f"not let this survey read {', '.join(child.unreadable)}"
            )
            pending.append(child.pid)

    return Ownership(
        own=tuple(
            Attribution(process=by_pid[pid], reasons=tuple(reasons[pid]))
            for pid in sorted(reasons)
        ),
        unattributed=tuple(
            Attribution(process=by_pid[pid], implicated=opaque[pid])
            for pid in sorted(opaque)
        ),
    )


def holding(
    paths: Sequence[str],
    *,
    lister: Callable[[], Iterable[Process]] | None = None,
    exclude: Sequence[int] = (),
    roots: Sequence[int] = (),
) -> list[Process]:
    """Return the processes established to be this run's, and nothing else.

    This is the attributed half of `attribute`. A caller that has to act on
    what it finds should ask for the whole answer instead: the half this drops
    is the half nobody can rule out.
    """
    return [
        item.process
        for item in attribute(
            paths, lister=lister, exclude=exclude, roots=roots
        ).own
    ]


def _tree(processes: Sequence[Process], root: int) -> list[Process]:
    """Return every descendant of `root` in this table."""
    children: dict[int, list[Process]] = {}
    for process in processes:
        children.setdefault(process.parent, []).append(process)
    found: list[Process] = []
    pending = [root]
    seen = {root}
    while pending:
        current = pending.pop()
        for child in children.get(current, ()):
            if child.pid in seen:
                continue
            seen.add(child.pid)
            found.append(child)
            pending.append(child.pid)
    return found


def _live_processes() -> list[Process]:
    found = []
    for entry in PROC_ROOT.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        command = _read(pid, "cmdline")
        # A command line is readable whoever owns the process, so an empty one
        # is a kernel thread or a process on its way out. Neither holds a path.
        if not command:
            continue
        denied: list[str] = []
        cwd = _readlink(pid, "cwd", denied, "its working directory")
        home, session = _read_environment(pid, denied)
        namespace = _readlink(pid, "ns/mnt", denied, "its mount namespace")
        found.append(
            Process(
                pid=pid,
                command=command,
                parent=_read_parent(pid),
                home=home,
                cwd=cwd,
                session=session,
                namespace=namespace,
                unreadable=tuple(denied),
            )
        )
    return found


def _send(
    signaller: Callable[[int, int], None],
    pid: int,
    number: int,
    refused: dict[int, str],
) -> None:
    """Signal one process, and keep what the host said when it would not.

    A process that has already gone is the ordinary outcome and says nothing.
    Any other refusal — `EPERM`, for a process running as an identifier this
    runner cannot reach — is kept, because a survey that swallows it counts a
    process it never touched as one it stopped.
    """
    try:
        signaller(pid, number)
    except ProcessLookupError:
        refused.pop(pid, None)
    except OSError as error:
        refused[pid] = f"{type(error).__name__}: {error}"


def survey_and_stop(
    paths: Sequence[str],
    *,
    lister: Callable[[], Iterable[Process]] | None = None,
    signaller: Callable[[int, int], None] = os.kill,
    sleeper: Callable[[float], None] = time.sleep,
    grace_seconds: float = 5.0,
    exclude: Sequence[int] = (),
    roots: Sequence[int] = (),
    host_namespace: str | None = None,
) -> SurveyReport:
    """Stop every process established to be this run's, and confirm it.

    Nothing is signalled at all while any process near this run is
    unattributed. Stopping the ones that were established and leaving the rest
    unmentioned would be the worst of both: it acts, and it reports a host it
    did not establish anything about.
    """

    def survey() -> Ownership:
        return attribute(
            paths,
            lister=lister,
            exclude=exclude,
            roots=roots,
            host_namespace=host_namespace,
        )

    owned = survey()
    report = SurveyReport(
        found=list(owned.pids),
        attribution={
            item.pid: "; ".join(item.reasons) for item in owned.own
        },
        unattributed=[item.pid for item in owned.unattributed],
        unreadable={item.pid: item.implicated for item in owned.unattributed},
    )
    if owned.unattributed:
        # `found` stands: it is what this would have signalled, and an
        # operator reading a refusal is owed the list it was refused over.
        report.detail = (
            f"{len(owned.unattributed)} process(es) near this run could not be "
            "established to be its own or to be somebody else's, so nothing "
            "was signalled: "
            + "; ".join(item.describe() for item in owned.unattributed)
        )
        return report
    if not owned.own:
        report.detail = "no process on the host was still holding a path of this run"
        return report

    for item in owned.own:
        _send(signaller, item.pid, signal.SIGTERM, report.refused)
    sleeper(grace_seconds)
    still = survey()
    for item in still.own:
        _send(signaller, item.pid, signal.SIGKILL, report.refused)
    if still.own:
        sleeper(1.0)
    remaining = survey()
    report.surviving = list(remaining.pids)
    report.unattributed = [item.pid for item in remaining.unattributed]
    report.unreadable.update(
        {item.pid: item.implicated for item in remaining.unattributed}
    )
    report.stopped = [pid for pid in report.found if pid not in report.surviving]
    report.detail = (
        f"{len(report.stopped)} process(es) the run had started were still on the "
        "host and were stopped"
        if report.clean
        else (
            f"{len(report.surviving)} process(es) the run had started would not stop "
            "and are still on the host"
            + (
                "; the host refused the signal for "
                + ", ".join(
                    f"pid {pid} ({why})" for pid, why in sorted(report.refused.items())
                )
                if report.refused
                else ""
            )
        )
    )
    return report
