"""A picture of the environment's own screen, taken from outside the application.

The window check next door establishes *where* the application's window went,
by listing what is on this run's screen before and after it was started. What
that produces is identifiers: a window exists, it has a title, it was not there
a moment ago. A reader who wants to see the two agents' terminals — the panels
the whole handoff is about — has nothing to look at.

So a run also takes an image, and three things decide how.

**It is taken from outside the application.** A screenshot an application
produces of itself is a claim by the thing under test, and the thing under test
is what this runner exists to doubt. The machine backend is therefore asked
through the hypervisor, from the host, which never enters the guest at all; the
container backend has no hypervisor, so its capture comes from the X server
that is drawing the window, on the box's own display.

**It is never the operator's screen.** The box has the host's `/tmp` mounted,
so the operator's X socket is reachable from inside it and a capture pointed at
the wrong screen number would write somebody's desktop into a shared artifact
directory. A capture taken inside the environment therefore names the screen
this run created and refuses every other one — including the case where the
configuration asked for the operator's screen deliberately, because accepting a
window on their desktop is not consent to photograph it. A capture taken by the
hypervisor is addressed by domain rather than by screen number and can reach
nothing else, which is why the two are judged apart.

**It is evidence, not a gate.** A screen that did not answer, a tool the image
was never built with, an image that never reached the host: each is recorded
with its reason, and none of them stops a run. Nothing the runner claims rests
on a picture, so a missing one cannot be allowed to cost a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import display as display_module, hashing
from .result import PhaseStatus, ScreenshotRecord

#: Where a capture was taken from. Only one of them is pointed at a screen by
#: number, and only that one can be pointed at the wrong screen.
HYPERVISOR = "hypervisor"
X_SERVER = "x-server"

#: The two moments worth an image: when the runtime is ready and the window
#: check has just run, and when the review has returned and both agents'
#: terminals exist at the same time.
RUNTIME_READY = "runtime-ready"
AFTER_REVIEW = "after-review"

#: Where the images sit, inside the environment and among the artifacts.
DIRECTORY = "screenshots"

#: Read the root window through the X server and write it out. `xwd` is the X
#: server's own client: the application is not asked for anything and is not
#: told it was photographed. The dump is converted to a png when the tools for
#: it are installed, because an image nobody can open is not evidence either.
CAPTURE_SCRIPT = r"""
set -u
export DISPLAY="$1"
directory="$2"
if [ -z "$directory" ]; then
    printf 'screen %s not captured\n' "$DISPLAY"
    printf 'reason no directory was named to write the image into\n'
    exit 0
fi
if ! xset -q > /dev/null 2>&1; then
    printf 'screen %s not captured\n' "$DISPLAY"
    printf 'reason the screen did not answer\n'
    exit 0
fi
mkdir -p "$directory"
rm -f "$directory/screen.xwd" "$directory/screen.png"
if ! xwd -display "$DISPLAY" -root -silent -out "$directory/screen.xwd" 2>/dev/null; then
    printf 'screen %s not captured\n' "$DISPLAY"
    printf 'reason xwd could not read the root window of this screen\n'
    exit 0
fi
name=screen.xwd
if command -v xwdtopnm > /dev/null 2>&1 && command -v pnmtopng > /dev/null 2>&1; then
    if xwdtopnm < "$directory/screen.xwd" 2>/dev/null \
        | pnmtopng > "$directory/screen.png" 2>/dev/null \
        && [ -s "$directory/screen.png" ]; then
        rm -f "$directory/screen.xwd"
        name=screen.png
    else
        rm -f "$directory/screen.png"
    fi
fi
printf 'screen %s captured\n' "$DISPLAY"
printf 'file %s\n' "$name"
printf 'bytes %s\n' "$(wc -c < "$directory/$name")"
printf 'sha256 %s\n' "$(sha256sum "$directory/$name" | cut -d' ' -f1)"
"""


def capture_argv(display: str, directory: str) -> list[str]:
    """Return the command that photographs one screen through its X server.

    The screen is passed as an argument rather than inherited, for the same
    reason the window check passes it: a command that takes whichever `DISPLAY`
    it is handed takes the operator's when something re-introduces it.
    """
    return ["sh", "-c", CAPTURE_SCRIPT, "cycle-screenshot", display, directory]


def parse(stdout: str) -> dict[str, Any]:
    """Return what the capture command said it did, field by field."""
    reported: dict[str, Any] = {
        "captured": False,
        "display": "",
        "file": "",
        "bytes": 0,
        "sha256": "",
        "reason": "",
    }
    for line in stdout.splitlines():
        parts = line.split(None, 2)
        if not parts:
            continue
        if parts[0] == "screen" and len(parts) >= 3:
            reported["display"] = parts[1]
            reported["captured"] = parts[2].strip() == "captured"
        elif parts[0] == "reason" and len(parts) >= 2:
            reported["reason"] = line.split(None, 1)[1].strip()
        elif parts[0] in ("file", "sha256") and len(parts) >= 2:
            reported[parts[0]] = parts[1]
        elif parts[0] == "bytes" and len(parts) >= 2:
            try:
                reported["bytes"] = int(parts[1])
            except ValueError:
                pass
    return reported


def permitted(
    *, source: str, mode: str, display: str, operator_display: str
) -> tuple[bool, str]:
    """Say whether this run may photograph this screen at all, and why not.

    A hypervisor is asked for one named domain's framebuffer and has no way to
    address anything else, so there is nothing here to refuse. A capture taken
    inside the environment is pointed at a screen by number, in a box where the
    operator's X socket is mounted, and a wrong number there is their desktop.
    """
    if source == HYPERVISOR:
        return True, (
            "the hypervisor holds this domain's framebuffer and can address no "
            "other screen"
        )
    if mode == display_module.HOST:
        return False, (
            "this run was configured to use the operator's own screen; a window "
            "on their desktop was the declared compromise, and photographing "
            "that desktop into a shared artifact directory is not part of it"
        )
    if not display.strip():
        return False, (
            "nothing names a screen, and a capture with no screen named takes "
            "whichever one the command inherits"
        )
    if operator_display and display == operator_display:
        return False, (
            f"{display} is the screen the operator's own session is on, and this "
            "environment has their X socket mounted"
        )
    return True, f"{display} is the screen this run created"


@dataclass(frozen=True)
class Capture:
    """One attempt at an image, and whether there is anything to look at."""

    moment: str
    source: str
    display: str
    artifact: str = ""
    size_bytes: int = 0
    sha256: str = ""
    ok: bool = False
    detail: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "moment": self.moment,
            "source": self.source,
            "display": self.display,
            "artifact": self.artifact,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "ok": self.ok,
            "detail": self.detail,
        }


def judge(
    *,
    moment: str,
    source: str,
    display: str,
    artifact: str = "",
    data: bytes | None = None,
    reported: Mapping[str, Any] | None = None,
    failure: str = "",
) -> Capture:
    """Turn one attempt into the record of it; nothing here raises or gates.

    What is judged is the bytes the host holds, not the capture's own report of
    itself: a command that says it wrote an image and a host that has one are
    different claims, and only the second is worth exporting.
    """
    attempt = {"moment": moment, "source": source, "display": display}
    if failure:
        return Capture(**attempt, detail=failure)
    reported = dict(reported or {})
    if reported and not reported.get("captured"):
        return Capture(
            **attempt,
            detail=(
                "the capture did not happen: "
                + (str(reported.get("reason") or "") or "nothing said why")
            ),
        )
    if not data:
        return Capture(
            **attempt,
            detail=(
                "no image reached the host, so there is nothing to look at: "
                + (str(reported.get("reason") or "") or "nothing said why")
            ),
        )
    observed = hashing.digest_bytes(data)
    claimed = str(reported.get("sha256") or "")
    if claimed and claimed != observed:
        return Capture(
            **attempt,
            artifact=artifact,
            size_bytes=len(data),
            sha256=observed,
            detail=(
                "the image the host holds is not the one the capture reported "
                "taking, so it is not a picture of this screen"
            ),
        )
    return Capture(
        **attempt,
        artifact=artifact,
        size_bytes=len(data),
        sha256=observed,
        ok=True,
        detail=(
            f"{len(data)} byte(s) of the screen {display}, taken by the {source} "
            "rather than by the application"
        ),
    )


def record(display: str, captures: Sequence[Capture]) -> ScreenshotRecord:
    """Turn the attempts into the record the run exports.

    A capture that failed is a capture that failed and nothing more. This
    record is the picture of the panels; no phase consults its status, and a
    run that could not take one is not a worse run than one that did.
    """
    if not captures:
        return ScreenshotRecord(
            status=PhaseStatus.SKIPPED,
            display=display,
            detail="this run took no picture of its own screen",
        )
    documents = [capture.to_document() for capture in captures]
    landed = [capture for capture in captures if capture.ok]
    if len(landed) == len(captures):
        return ScreenshotRecord(
            status=PhaseStatus.OK,
            display=display,
            captures=documents,
            detail=(
                f"{len(landed)} picture(s) of the screen this run created, each "
                "taken from outside the application: "
                + ", ".join(f"{item.moment} ({item.artifact})" for item in landed)
            ),
        )
    unresolved = "; ".join(
        f"{item.moment}: {item.detail}" for item in captures if not item.ok
    )
    return ScreenshotRecord(
        status=PhaseStatus.FAILED,
        display=display,
        captures=documents,
        detail=(
            f"{len(landed)} of {len(captures)} picture(s) reached the host, and "
            f"nothing this run claims rests on the rest — {unresolved}"
        ),
    )
