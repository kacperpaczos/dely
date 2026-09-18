"""What a run that never finished left in its own state directory.

Cleanup is the end of the cycle, so it only ever runs for a run that reached
its end. A run that was killed, interrupted or lost with the terminal it was
started from never reaches it, and its per-run state — everything under
`<state_root>/<run_id>` — stays on the host with nothing that ever notices.

Three private transport keys were found that way, at
`<state_root>/<run_id>/id_cycle`, belonging to domains that no longer existed.
The disk space was never the point. The discipline is that nothing of a run
outlives it, and there were already rails for the two other ways that can
fail: a process still holding a path (`processes`) and an entry in the
operator's own registry (`hostregistry`). This is the third.

Two things it will not do.

It does not guess what a leftover directory *is*. Every name below annotates;
nothing here decides from a name. A file is called key material because its
bytes open a private key block, so a key written under a dull name is still
named as a key, and a file merely called `id_cycle` that holds a public half
is not. That is the same rule the process survey follows for a different
reason: a name is what reaches something that was never this run's.

A private key is not the only secret a killed run leaves. The container
backend mints no transport key at all, and the `existing_login` auth method
copies the operator's own login into the per-run home on every run — its own
receipt records `removed_after_run` as false. A killed run leaves that file
sitting at mode 600 under the state root, and a survey that asked only about
private key blocks reported the host as carrying none. It was reporting
truthfully about the wrong question. So credential material is named too, by
the same rule and from the same bytes: what `redact` recognises as a
credential *value*, wherever it is and whatever it is called.

The two stay separate names because they are separate facts, and a private
key is reported as a private key rather than folded into the broader word. A
false positive here costs a line in a report and nothing else: naming
something is not what refuses a removal.

And it removes nothing on its own. `discard` takes what a caller established
about the run — the processes still holding its paths, the resources of it the
host says are still there, and the questions this host could not answer at
all — and refuses on any of them. A question this host cannot ask is not a
question it was told no to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import ids, redact

#: How many directory entries one survey visits before it stops. A per-run
#: home carries a project copy and an application profile, so the walk is
#: bounded; a survey that stopped says so rather than reading as a complete one.
MAX_ENTRIES = 20000

#: How much of each file is read to decide whether it opens a private key.
#: A key block's first line is under forty bytes; this much also catches one
#: written underneath a short preamble, and bounds what a survey reads.
SNIFF_BYTES = 4096

#: What each thing the adapters write into a per-run state directory is, in
#: the terms of the backend that writes it. This annotates a report for a
#: reader. It decides nothing: see the module docstring.
ROLES = {
    "home": (
        "the container backend's per-run home — the project copy, the "
        "application profile, and whatever the auth method copied in"
    ),
    "distrobox.ini": "the assemble manifest the box was created from",
    "id_cycle": "the machine backend's per-run transport private key",
    "id_cycle.pub": "the public half the guest's cloud-init seed authorised",
    "known_hosts": "the guest host keys this run's transport accepted",
    "stack": (
        "the pulumi program for this run's domain, and the local state beside it"
    ),
}

UNNAMED_ROLE = "left by this run; this runner does not name it"

KEY_MATERIAL = "private key material"

CREDENTIAL_MATERIAL = "credential material"


class ResidueRefused(RuntimeError):
    """Nothing established that this run is over, so nothing was removed."""


@dataclass(frozen=True)
class Entry:
    """One thing at the top of a per-run state directory, and what it holds."""

    name: str
    is_directory: bool
    file_count: int = 0
    size_bytes: int = 0
    #: Relative paths under this entry whose bytes open a private key block.
    key_material: tuple[str, ...] = ()
    #: Relative paths under this entry whose bytes carry a credential value.
    credential_material: tuple[str, ...] = ()
    role: str = UNNAMED_ROLE

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "is_directory": self.is_directory,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "key_material": list(self.key_material),
            "credential_material": list(self.credential_material),
            "role": self.role,
        }


@dataclass(frozen=True)
class StateResidue:
    """What one run left under the state root, and what of it matters."""

    run_id: str
    path: Path
    present: bool = False
    file_count: int = 0
    size_bytes: int = 0
    entries: tuple[Entry, ...] = ()
    #: Every path, relative to the directory, whose bytes open a private key.
    key_material: tuple[str, ...] = ()
    #: Every path whose bytes carry a credential value but not a private key.
    credential_material: tuple[str, ...] = ()
    #: Paths that could not be read, so what they hold is not established.
    unreadable: tuple[str, ...] = ()
    #: Symbolic links, which are counted and never followed or opened.
    links: tuple[str, ...] = ()
    #: Whether the walk reached the end of the tree within its bound.
    complete: bool = True
    detail: str = ""

    @property
    def carries_key_material(self) -> bool:
        return bool(self.key_material)

    @property
    def carries_credential_material(self) -> bool:
        return bool(self.credential_material)

    @property
    def carries_a_secret(self) -> bool:
        """Whether anything in here is a secret of either kind.

        What an operator wants from one glance is whether this directory is
        dangerous, not which of the two words applies to it.
        """
        return self.carries_key_material or self.carries_credential_material

    def headline(self) -> str:
        """One clause, for a report that is already talking about this run."""
        if not self.present:
            return f"it left nothing at {self.path}"
        said = f"it left {self.file_count} file(s) at {self.path}"
        if self.key_material:
            said += (
                f", {KEY_MATERIAL} among them ({', '.join(self.key_material)})"
            )
        if self.credential_material:
            said += (
                f", {CREDENTIAL_MATERIAL} among them "
                f"({', '.join(self.credential_material)})"
            )
        if not self.complete:
            said += f", and the walk stopped at {MAX_ENTRIES} entries"
        return said

    def describe(self) -> str:
        """The whole of what this run left, named line by line."""
        lines = [f"{self.run_id}: {self.headline()}"]
        for entry in self.entries:
            shape = "dir " if entry.is_directory else "file"
            lines.append(
                f"    {shape} {entry.name:16} {entry.file_count:6} file(s) "
                f"{entry.size_bytes:12} byte(s)  {entry.role}"
            )
            for relative in entry.key_material:
                lines.append(f"         {KEY_MATERIAL}: {relative}")
            for relative in entry.credential_material:
                lines.append(f"         {CREDENTIAL_MATERIAL}: {relative}")
        for relative in self.unreadable:
            lines.append(f"    unreadable: {relative}")
        if not self.complete:
            lines.append(
                f"    the walk stopped at {MAX_ENTRIES} entries; what lies "
                "beyond it was not looked at"
            )
        return "\n".join(lines)

    def to_document(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": str(self.path),
            "present": self.present,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "entries": [entry.to_document() for entry in self.entries],
            "key_material": list(self.key_material),
            "carries_key_material": self.carries_key_material,
            "credential_material": list(self.credential_material),
            "carries_credential_material": self.carries_credential_material,
            "carries_a_secret": self.carries_a_secret,
            "unreadable": list(self.unreadable),
            "links": list(self.links),
            "complete": self.complete,
            "detail": self.detail or self.headline(),
        }


@dataclass
class _Branch:
    """One top-level name, as the walk fills it in."""

    is_directory: bool
    file_count: int = 0
    size_bytes: int = 0
    key_material: list[str] = field(default_factory=list)
    credential_material: list[str] = field(default_factory=list)


def _opens_a_private_key(path: Path, *, sniff_bytes: int) -> bool:
    """Report whether this file's first bytes open a private key block."""
    with open(path, "rb") as handle:
        return redact.carries_private_key(handle.read(sniff_bytes))


def _carries_a_credential(path: Path, *, sniff_bytes: int) -> bool:
    """Report whether this file's first bytes carry a credential *value*.

    `redact.carries_credential_shape` is the narrow question: something shaped
    like a secret is present, rather than a name that says one is expected. A
    configuration that declares which variable will hold a token is therefore
    not named, and the login this runner's auth method copies into the per-run
    home is, because the copy holds the token itself.

    The file is opened a second time rather than once for both questions. Each
    question then stands on its own line and has its own counterexample, and
    the read is the same bounded, page-cached one the key question makes.
    """
    with open(path, "rb") as handle:
        return redact.carries_credential_shape(
            handle.read(sniff_bytes).decode("utf-8", errors="replace")
        )


def state_path(root: Path, run_id: str) -> Path:
    """Return the per-run state directory, which is what a run leaves behind."""
    return Path(root) / run_id


def survey(
    root: Path,
    run_id: str,
    *,
    max_entries: int = MAX_ENTRIES,
    sniff_bytes: int = SNIFF_BYTES,
) -> StateResidue:
    """Report what one run left under the state root, naming key material.

    Symbolic links are counted and never followed, so a link left pointing at
    the operator's own home is reported as a link rather than walked into and
    read.
    """
    if not ids.is_run_id(run_id):
        # The walk is confined to a name this runner mints. A lease document
        # carries its own `run_id` field, and a directory name is whatever is
        # on the disk; neither is allowed to point this at a path above it.
        return StateResidue(
            run_id=run_id,
            path=state_path(root, run_id),
            detail=f"{run_id!r} is not a run identifier of the documented shape",
        )
    base = state_path(root, run_id)
    if base.is_symlink():
        # A link is counted and never followed, at the top of the tree as
        # everywhere under it. Walking one here would read whatever it names,
        # which is the operator's own home if somebody put it there.
        return StateResidue(
            run_id=run_id,
            path=base,
            links=(".",),
            detail=(
                f"{base} is a symbolic link; it is not followed, so what "
                f"{run_id} left is not established from here"
            ),
        )
    if not base.is_dir():
        return StateResidue(
            run_id=run_id,
            path=base,
            detail=f"{run_id} left nothing under {base}",
        )

    branches: dict[str, _Branch] = {}
    key_material: list[str] = []
    credential_material: list[str] = []
    unreadable: list[str] = []
    links: list[str] = []
    visited = 0
    complete = True
    pending: list[tuple[Path, str]] = [(base, "")]

    while pending:
        directory, prefix = pending.pop()
        try:
            with os.scandir(directory) as scan:
                listing = sorted(scan, key=lambda item: item.name)
        except OSError:
            unreadable.append(prefix or ".")
            continue
        for item in listing:
            if visited >= max_entries:
                complete = False
                pending.clear()
                break
            visited += 1
            relative = f"{prefix}/{item.name}" if prefix else item.name
            top = relative.split("/", 1)[0]
            is_link = item.is_symlink()
            is_directory = item.is_dir(follow_symlinks=False)
            branch = branches.setdefault(
                top, _Branch(is_directory=is_directory if top == relative else True)
            )
            if is_directory:
                pending.append((Path(item.path), relative))
                continue
            try:
                size = item.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            branch.file_count += 1
            branch.size_bytes += size
            if is_link:
                links.append(relative)
                continue
            try:
                if _opens_a_private_key(Path(item.path), sniff_bytes=sniff_bytes):
                    key_material.append(relative)
                    branch.key_material.append(relative)
                elif _carries_a_credential(Path(item.path), sniff_bytes=sniff_bytes):
                    # Not `if`: a private key block is a credential shape too,
                    # and a key is reported as a key rather than twice.
                    credential_material.append(relative)
                    branch.credential_material.append(relative)
            except OSError:
                unreadable.append(relative)

    entries = tuple(
        Entry(
            name=name,
            is_directory=branch.is_directory,
            file_count=branch.file_count,
            size_bytes=branch.size_bytes,
            key_material=tuple(sorted(branch.key_material)),
            credential_material=tuple(sorted(branch.credential_material)),
            role=ROLES.get(name, UNNAMED_ROLE),
        )
        for name, branch in sorted(branches.items())
    )
    return StateResidue(
        run_id=run_id,
        path=base,
        present=True,
        file_count=sum(entry.file_count for entry in entries),
        size_bytes=sum(entry.size_bytes for entry in entries),
        entries=entries,
        key_material=tuple(sorted(key_material)),
        credential_material=tuple(sorted(credential_material)),
        unreadable=tuple(sorted(unreadable)),
        links=tuple(sorted(links)),
        complete=complete,
    )


#: The names only one backend's adapter ever writes into a per-run state
#: directory. A name is never allowed to say that something is gone. It is
#: allowed to say that the wrong question is being asked, because the failure
#: mode of a name is a false match, and a false match here costs a refusal.
WRITTEN_BY = {
    "distrobox.ini": "distrobox",
    "home": "distrobox",
    "id_cycle": "vm",
    "id_cycle.pub": "vm",
    "stack": "vm",
    "known_hosts": "vm",
}


def backend_that_wrote_it(left: StateResidue) -> str:
    """Return the backend whose adapter writes what is in here, or nothing.

    Two backends named, or none, returns the empty string: this exists to
    catch a directory being asked about by the backend that did not create
    it, and a mixed or unrecognised directory is not that.
    """
    named = {
        WRITTEN_BY[entry.name] for entry in left.entries if entry.name in WRITTEN_BY
    }
    return named.pop() if len(named) == 1 else ""


def run_identifiers(root: Path) -> list[str]:
    """Return every run-shaped directory directly under the state root.

    A lease is not the whole answer. A run killed before admission wrote its
    lease, or one whose lease somebody has already cleared, leaves a directory
    with nothing pointing at it; this is what finds those.
    """
    base = Path(root)
    if not base.is_dir():
        return []
    try:
        with os.scandir(base) as scan:
            listing = list(scan)
    except OSError:
        return []
    return sorted(
        item.name
        for item in listing
        if item.is_dir(follow_symlinks=False) and ids.is_run_id(item.name)
    )


def discard(
    *,
    root: Path,
    run_id: str,
    holders: Sequence[Any] = (),
    still_present: Sequence[Any] = (),
    unanswered: Sequence[Any] = (),
) -> StateResidue:
    """Remove one run's state, once somebody established the run is over.

    Every argument is something a caller looked for and could not rule out.
    `holders` is what a process survey found still holding this run's paths.
    `still_present` is what the host says is still there — its lease held by a
    live owner, its container, its domain, its overlay. `unanswered` is every
    question this host could not put at all. Anything in any of them refuses,
    and the report is returned to the caller either way, because what is
    uncertain is reported and never removed on a guess.
    """
    if not ids.is_run_id(run_id):
        raise ResidueRefused(
            f"{run_id!r} is not a run identifier of the documented shape; "
            "nothing is removed under a name this runner did not mint"
        )
    root = Path(root)
    left = survey(root, run_id)
    if not left.present:
        raise ResidueRefused(left.detail)
    if holders:
        listed = ", ".join(str(holder) for holder in holders)
        raise ResidueRefused(
            f"{run_id} still has processes on this host ({listed}); its state "
            "stays until they are gone"
        )
    if still_present:
        listed = ", ".join(str(item) for item in still_present)
        raise ResidueRefused(
            f"{run_id} is not gone: {listed} is still on this host, so what it "
            "left is state in use rather than state left behind"
        )
    if unanswered:
        listed = ", ".join(str(item) for item in unanswered)
        raise ResidueRefused(
            f"whether {run_id} is gone could not be established on this host "
            f"({listed}); a question this host cannot ask is not a question it "
            "was told no to"
        )
    # Imported here, not at the top: `cleanup` reaches the adapters, the
    # adapters reach the configuration, and the configuration reaches
    # admission, which reads this module. The removal still goes through the
    # one rail that refuses a path outside its root, which is the point.
    from . import cleanup

    cleanup.safe_remove(
        left.path, allowed_roots=[root], protected=[Path(root) / "leases"]
    )
    if left.path.exists():
        raise ResidueRefused(
            f"{left.path} is still present after it was removed; nothing here "
            "reports a removal it cannot see"
        )
    return left
