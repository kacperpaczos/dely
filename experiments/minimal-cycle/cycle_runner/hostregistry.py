"""What the operator's own Orca knows, before and after the run.

The environment gets its own Orca, its own profile and its own home. Nothing
guarantees that: a container inherits the host's session environment, and an
application that finds the host's socket will happily register a repository,
a worktree or a terminal into the host's profile. That is the failure this
watches for.

It does not watch for *any* change. The operator's Orca is running while the
run is, and this runner is often started from inside it, so its registry moves
for reasons that have nothing to do with the environment. What must never
happen is narrower and answerable: no entry in the host's registry may name
anything belonging to this run — not its identifier, not its container or
domain, not its per-run paths. The registry is searched for those names as
bytes, so a repository entry, a worktree, a terminal archive and a row in the
orchestration database are all caught by the same question.

Identifiers here are digested. The registry belongs to a person, and a run's
artifacts are shared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .probe import digest
from .proc import utc_now

#: The registry, as (path under the home, which file names count when it is a
#: directory). `None` means every file under it.
#:
#: Terminal scrollback is deliberately not here. A transcript of the operator's
#: own terminal carries the run identifier because the run printed it there;
#: that is somebody watching their run, not the environment registering itself.
#: What matters beside a terminal is the record that a terminal exists — the
#: worktree it belongs to — and that is in `meta.json`.
REGISTRY_SOURCES = (
    (".config/orca/profiles", None),
    (".config/orca/orchestration.db", None),
    (".config/orca/orchestration.db-wal", None),
    (".config/orca/orca-profile-index.json", None),
    (".config/orca/terminal-history", ("meta.json",)),
)

#: The collections in the profile document this reports the size of, because a
#: repository or a worktree appearing is what an escaped registration looks like.
COUNTED = (
    "repos",
    "projects",
    "projectHostSetups",
    "projectGroups",
    "folderWorkspaces",
    "worktreeMeta",
    "worktreeMetaByIdentity",
    "sshTargets",
    "automations",
)

#: Files larger than this are searched in chunks rather than read whole.
CHUNK_BYTES = 4 * 1024 * 1024


def registry_files(home: Path) -> list[Path]:
    """Return every file that carries part of the host's registry."""
    found: list[Path] = []
    for relative, names in REGISTRY_SOURCES:
        target = Path(home) / relative
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(
                sorted(
                    item
                    for item in target.rglob("*")
                    if item.is_file() and (names is None or item.name in names)
                )
            )
    return found


def _occurrences(path: Path, needles: Sequence[bytes]) -> int:
    """Count how many times any marker appears in this file's bytes."""
    total = 0
    longest = max((len(needle) for needle in needles), default=0)
    try:
        with open(path, "rb") as handle:
            carry = b""
            while True:
                block = handle.read(CHUNK_BYTES)
                if not block:
                    break
                window = carry + block
                for needle in needles:
                    total += window.count(needle)
                carry = window[-longest:] if longest else b""
    except OSError:
        # A file that cannot be read cannot be shown to be clean.
        return -1
    return total


def _collection_sizes(path: Path) -> dict[str, int]:
    import json

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, Mapping):
        return {}
    sizes: dict[str, int] = {}
    for name in COUNTED:
        value = document.get(name)
        if isinstance(value, (list, dict)):
            sizes[name] = len(value)
    for name in ("settings", "ui"):
        value = document.get(name)
        if isinstance(value, Mapping):
            sizes[name] = len(value)
    return sizes


def fingerprint(
    home: Path | None,
    *,
    markers: Iterable[str] = (),
) -> dict[str, Any]:
    """Describe the host's registry, and whether it names anything of this run."""
    home = Path(home) if home is not None else Path.home()
    needles = [marker.encode("utf-8") for marker in markers if str(marker).strip()]
    files = registry_files(home)
    entries: list[dict[str, Any]] = []
    naming: list[dict[str, Any]] = []
    unreadable: list[str] = []
    sizes: dict[str, int] = {}
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        hits = _occurrences(path, needles) if needles else 0
        entry = {
            "path_digest": digest(str(path)),
            "name": path.name,
            "size_bytes": size,
            "names_this_run": hits,
        }
        entries.append(entry)
        if hits < 0:
            unreadable.append(path.name)
        elif hits:
            naming.append(entry)
        if path.name == "orca-data.json":
            sizes = _collection_sizes(path)
    return {
        "captured_at": utc_now(),
        "home_digest": digest(str(home)),
        "present": bool(files),
        "file_count": len(files),
        "files": entries,
        "collection_sizes": sizes,
        "entries_naming_this_run": naming,
        "unreadable": unreadable,
        "markers_searched": len(needles),
    }


def difference(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Report how the host's registry moved while the run was happening."""
    was = before.get("collection_sizes") or {}
    now = after.get("collection_sizes") or {}
    changed = {
        name: {"before": was.get(name), "after": now.get(name)}
        for name in sorted(set(was) | set(now))
        if was.get(name) != now.get(name)
    }
    return {
        "collections_changed": changed,
        "files_before": before.get("file_count"),
        "files_after": after.get("file_count"),
    }


def verdict(after: Mapping[str, Any]) -> tuple[bool, str]:
    """Say whether the host's registry stayed free of this run.

    Churn is not the question — the operator's own application is running. The
    question is whether anything in their registry now names this run.
    """
    if not after.get("markers_searched"):
        return False, (
            "the host registry was searched for nothing, so it cannot be said to "
            "be free of this run"
        )
    unreadable = list(after.get("unreadable") or [])
    if unreadable:
        return False, (
            "part of the host registry could not be read, so it cannot be shown "
            f"to be free of this run: {', '.join(sorted(unreadable))}"
        )
    naming = list(after.get("entries_naming_this_run") or [])
    if naming:
        listed = ", ".join(
            f"{entry['name']} ({entry['names_this_run']} occurrence(s))"
            for entry in naming
        )
        return False, (
            "the environment wrote this run into the operator's own Orca "
            f"registry: {listed}"
        )
    if not after.get("present"):
        return True, "this host has no Orca registry for the run to have reached"
    return True, (
        f"none of the {after.get('file_count')} host registry file(s) names this run"
    )
