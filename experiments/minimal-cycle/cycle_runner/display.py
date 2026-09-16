"""Whose screen the environment's application actually went to.

Giving a run its own virtual screen does not put the application on it. On the
container backend the operator's compositor socket is mounted into the box, and
a toolkit left to choose found it and opened a window on their desktop — with
the run's own `DISPLAY` set the whole time. Setting a variable is not evidence,
and neither is the absence of a complaint.

So this asks two questions with answers.

The positive one: does a window exist on the screen this run created? If the
application is there, it is not somewhere else.

The negative one: does any process this run started still carry a variable
pointing at the operator's session? Those are stripped before every command, so
finding one means something re-introduced it.

That second question only ever answers in one direction, and the reason matters
here. Electron rewrites its own environment block to set its process title, so
`/proc/<pid>/environ` for the application this runner starts reads as empty —
the operator's own instance on this host reads that way now. Finding a variable
is conclusive; not finding one establishes nothing, and this says so rather
than counting it as a pass.

Neither question is about isolation. The sockets stay reachable by hand for
anything that goes looking, and the record says so rather than implying a
sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: The modes a configuration may ask for.
VIRTUAL = "virtual"
HOST = "host"
MODES = (VIRTUAL, HOST)

#: Names that can point at the operator's own session.
HOST_SESSION_NAMES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)

WINDOW_SCRIPT = r"""
set -u
export DISPLAY="$1"
if ! xset -q > /dev/null 2>&1; then
    printf 'display %s unreachable\n' "$DISPLAY"
    exit 0
fi
printf 'display %s reachable\n' "$DISPLAY"
for id in $(xdotool search --onlyvisible --name '.*' 2>/dev/null); do
    name="$(xdotool getwindowname "$id" 2>/dev/null || printf '<unnamed>')"
    printf 'window %s %s\n' "$id" "$name"
done
"""


def windows_argv(display: str) -> list[str]:
    """Return the command that lists the visible windows on one screen."""
    return ["sh", "-c", WINDOW_SCRIPT, "cycle-display", display]


@dataclass(frozen=True)
class Window:
    identifier: str
    name: str

    def to_document(self) -> dict[str, str]:
        return {"id": self.identifier, "name": self.name}


def parse(stdout: str) -> tuple[bool, list[Window]]:
    """Return whether the screen answered, and the windows it reported."""
    reachable = False
    windows: list[Window] = []
    for line in stdout.splitlines():
        parts = line.split(None, 2)
        if not parts:
            continue
        if parts[0] == "display":
            reachable = len(parts) > 2 and parts[2] == "reachable"
        elif parts[0] == "window" and len(parts) >= 2:
            windows.append(
                Window(identifier=parts[1], name=parts[2] if len(parts) > 2 else "")
            )
    return reachable, windows


def named(windows: Iterable[Window], needle: str) -> list[Window]:
    """Return the windows whose name mentions this, case-insensitively."""
    lowered = needle.lower()
    return [window for window in windows if lowered in window.name.lower()]


def pointing_at_the_operator(
    session: Mapping[str, str], *, host_paths: Sequence[str], expected_display: str
) -> list[str]:
    """Return the names of this process's variables that reach the operator.

    Presence decides nothing. A message bus address under the environment's own
    runtime directory is the environment's own bus, and the application sets one
    for itself; the same name pointing at `/run/user/<uid>` is the leak. So the
    value is what is judged — where it points, never what it is called.

    `DISPLAY` is the exception, because its value is a screen number rather than
    a path: anything but the screen this run created reaches another one.
    """
    reaching: list[str] = []
    wanted = [str(path) for path in host_paths if str(path).strip()]
    for name, value in session.items():
        if not value:
            continue
        if name == "DISPLAY":
            if expected_display and value != expected_display:
                reaching.append(name)
            continue
        if name == "WAYLAND_DISPLAY":
            # A socket name, meaningless without the runtime directory that
            # holds it; that directory is judged on its own line.
            continue
        if any(path in value for path in wanted):
            reaching.append(name)
    return reaching


def carrying_host_session(
    processes: Sequence[Any],
    *,
    host_namespace: str = "",
    host_paths: Sequence[str] = (),
    expected_display: str = "",
) -> list[tuple[Any, list[str]]]:
    """Return the environment's processes that reach the operator, and how.

    Only processes *inside* the environment count. The host-side plumbing that
    launches into a container matches this run's paths as well — the run's own
    home is on its command line — and it carries the operator's session because
    it is the operator's process. Counting those reports every run as leaking,
    including the probe that is doing the counting.

    The mount namespace is what separates them, rather than a program name.
    """
    found: list[tuple[Any, list[str]]] = []
    for process in processes:
        if not process.inside(host_namespace):
            continue
        reaching = pointing_at_the_operator(
            getattr(process, "session", {}) or {},
            host_paths=host_paths,
            expected_display=expected_display,
        )
        if reaching:
            found.append((process, reaching))
    return found


def appeared(before: Sequence[Window], after: Sequence[Window]) -> list[Window]:
    """Return the windows that were not on this screen before the run started it."""
    known = {window.identifier for window in before}
    return [window for window in after if window.identifier not in known]


def describe(found: Sequence[Any], limit: int = 4) -> str:
    """Name what was found well enough to act on.

    The variable names are reported and their values are not: a value here is a
    path in somebody's home, and a run's artifacts are shared.
    """
    pieces = []
    for entry in found[:limit]:
        process, reaching = entry if isinstance(entry, tuple) else (entry, [])
        pid = getattr(process, "pid", "?")
        command = str(getattr(process, "command", "")).strip()
        pieces.append(f"pid {pid} [{', '.join(reaching) or 'nothing named'}] {command[:120]}")
    if len(found) > limit:
        pieces.append(f"and {len(found) - limit} more")
    return "; ".join(pieces)


def verdict(
    *,
    mode: str,
    reachable: bool,
    before: Sequence[Window],
    after: Sequence[Window],
    application: str,
    leaking: Sequence[Any] = (),
) -> tuple[bool, str]:
    """Say whether this run kept its application off the operator's desktop.

    A window is recognised two ways, because neither alone is reliable: one that
    was not on this screen before the run started the application, and one whose
    title names the application. The first does not depend on a title; the
    second still answers when the application was already running.
    """
    if mode == HOST:
        return True, (
            "this run was configured to use the operator's own screen, so a "
            "window on their desktop is what was asked for"
        )
    if leaking:
        return False, (
            "processes inside this environment still carry a variable pointing "
            "at the operator's session, so something re-introduced it after it "
            "was stripped: " + describe(leaking)
        )
    if not reachable:
        return False, (
            "the screen this run created did not answer, so nothing here knows "
            "where the application went"
        )
    new = appeared(before, after)
    matching = named(after, application)
    if new:
        return True, (
            f"{len(new)} window(s) appeared on the screen this run created that "
            f"were not there before it started the application, of {len(after)} "
            "there now. This is where it rendered; that no process was seen "
            "carrying a host session variable is not part of the finding, "
            "because the application rewrites its own environment block"
        )
    if matching:
        return True, (
            f"{application} has {len(matching)} window(s) on the screen this run "
            f"created; none appeared during this run, so it was already there"
        )
    return False, (
        f"the screen this run created answered and holds {len(after)} window(s), "
        f"none new and none {application}'s; a window that is not here is "
        "somewhere, and nothing here says where"
    )
