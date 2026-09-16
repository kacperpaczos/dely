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
from typing import Any, Iterable, Sequence

#: The modes a configuration may ask for.
VIRTUAL = "virtual"
HOST = "host"
MODES = (VIRTUAL, HOST)

#: Names that point at the operator's own session. A process of this run that
#: carries one was pointed at their screen.
HOST_SESSION_NAMES = ("WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")

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


def carrying_host_session(
    processes: Sequence[Any], *, host_namespace: str = ""
) -> list[Any]:
    """Return the environment's processes that still point at the operator's session.

    Only processes *inside* the environment count. The host-side plumbing that
    launches into a container matches this run's paths as well — the run's own
    home is on its command line — and it carries the operator's session because
    it is the operator's process. Counting those reports every run as leaking,
    including the probe that is doing the counting.

    The mount namespace is what separates them, rather than a program name.
    """
    return [
        process
        for process in processes
        if any(f"{name}=" in getattr(process, "session", "") for name in HOST_SESSION_NAMES)
        and process.inside(host_namespace)
    ]


def appeared(before: Sequence[Window], after: Sequence[Window]) -> list[Window]:
    """Return the windows that were not on this screen before the run started it."""
    known = {window.identifier for window in before}
    return [window for window in after if window.identifier not in known]


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
        listed = ", ".join(str(getattr(item, "pid", item)) for item in leaking)
        return False, (
            "processes of this run still carry a variable pointing at the "
            f"operator's session ({listed}); something re-introduced it after it "
            "was stripped"
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
