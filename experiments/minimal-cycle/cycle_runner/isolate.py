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

The host's *toolchain* leaks by the same route and was found the same way. A
box inherits the host's ``PATH`` literally, and on this host that path opens
with directories under the operator's home. So the box installed a pinned node
and a pinned Claude Code into ``/usr/local``, and then every bare program name
inside it — including the one the execution plane uses to launch an agent —
resolved to the operator's own build instead. The provisioning steps said so
out loud and nobody read it: a step that unpacked node v24.21.0 ended by
printing v24.19.0, because that is what ``node`` meant in there.

A search path is not a session variable, so it cannot be unset; the box needs
one. What it can be is *ordered*. The environment's own program directories go
in front of whatever was inherited, which is what makes the box's installation
win for anything launched inside it while leaving the rest of the inherited
path reachable. Nothing is removed: the mounted home still carries the
credential this run deliberately reaches, and an entry the box has no
replacement for still resolves.
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


#: The environment's own program directories, in the order a system lists them.
#: They are put in front of the inherited search path, so a program the
#: environment installed is the one a bare name finds there.
OWN_DIRECTORIES = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)


def own_toolchain_first(path: str) -> str:
    """Return this search path with the environment's own directories in front.

    Prepending rather than replacing is deliberate. The container backend
    mounts the operator's home on purpose — that is where the credential comes
    from — and entries under it may be the only place some tool lives. What
    must not happen is that they answer for a tool the environment installed
    itself, and ordering is enough to decide that.
    """
    return ":".join((*OWN_DIRECTORIES, *(part for part in [path] if part)))


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
    # The search path is ordered here rather than in any one caller, because
    # what this has to reach is the application the runner starts and every
    # agent that application spawns afterwards, none of which this runner
    # launches itself.
    # An empty inherited path must not leave a trailing separator: that is the
    # working directory, and putting it on a search path is how a file a task
    # just wrote gets run as a program.
    'if [ -n "${PATH:-}" ]; then PATH="' + ":".join(OWN_DIRECTORIES) + ':$PATH"; '
    'else PATH="' + ":".join(OWN_DIRECTORIES) + '"; fi; export PATH; '
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
