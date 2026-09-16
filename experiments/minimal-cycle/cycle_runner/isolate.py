"""Keeping the host's own session out of the environment.

This runner is usually launched from inside the very execution plane it is
testing, and that plane identifies its terminals through the environment. A
backend that inherits the host's environment therefore hands the environment
the host's session identity — and, in Orca's case, a token with it.

The effect is not subtle: the environment's own command line attests as the
host's terminal and refuses to act, which is the plane correctly noticing that
two different things claim to be the same session.

The operator's *display* leaks the same way and costs more. Distrobox mounts
`/run/user/<uid>`, which holds the compositor's socket, and `/tmp`, which holds
the X server's — so the host's screen is reachable from inside the box whatever
the environment says. What decides whether an application goes there is which
socket it is pointed at, and a toolkit that finds `WAYLAND_DISPLAY` set will
prefer the compositor over any `DISPLAY` the run chose. That is how a run that
had its own virtual screen still opened a window on somebody's desktop.

So these are removed too. Removing them is not isolation and this module does
not pretend otherwise: the sockets are still there for anything that goes
looking by hand. It is what stops a toolkit finding them by default.
"""

from __future__ import annotations

from typing import Mapping, Sequence

#: Variables whose names begin with these belong to the host's session, not to
#: the environment's. They are removed before any command runs inside one.
LEAKING_PREFIXES = ("ORCA_",)

#: Variables that point at the operator's own screen, sound and message bus.
#: Named one by one because they share no prefix.
LEAKING_NAMES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_ID",
    "XDG_SESSION_DESKTOP",
    "XDG_CURRENT_DESKTOP",
    "DBUS_SESSION_BUS_ADDRESS",
    "GDK_BACKEND",
    "QT_QPA_PLATFORM",
    "ELECTRON_OZONE_PLATFORM_HINT",
)


def _cases() -> str:
    patterns = [f"{prefix}*" for prefix in LEAKING_PREFIXES]
    patterns.extend(LEAKING_NAMES)
    return "|".join(patterns)


#: Unsets by prefix as well as by name: the plane may add a variable tomorrow,
#: and a list of names alone would silently stop covering it.
_SCRIPT = (
    'for prefix in "$@"; do '
    '  case "$prefix" in --) shift; break;; esac; '
    'done; '
    'for name in $(env | sed -n "s/^\\([A-Za-z_][A-Za-z0-9_]*\\)=.*/\\1/p"); do '
    '  case "$name" in '
    + _cases()
    + ') unset "$name";; '
    '  esac; '
    'done; '
    'exec "$@"'
)


def leaks(name: str) -> bool:
    """Report whether this variable belongs to the operator's own session."""
    return name in LEAKING_NAMES or any(
        name.startswith(prefix) for prefix in LEAKING_PREFIXES
    )


def scrubbed(environ: Mapping[str, str]) -> dict[str, str]:
    """Return this environment without what points at the operator's session.

    Stripping inside the environment is not enough for the command that
    *creates* one. A container's first process inherits whatever started it, so
    a box created from an unscrubbed environment holds the operator's display
    at its root, and everything descending from it that this runner did not
    launch — an agent's own child, say — inherits it in turn.
    """
    return {name: value for name, value in environ.items() if not leaks(name)}


def without_host_session(argv: Sequence[str]) -> list[str]:
    """Return an argv that runs the same command with the host's session removed."""
    return ["sh", "-c", _SCRIPT, "cycle-isolate", "--", *[str(item) for item in argv]]
