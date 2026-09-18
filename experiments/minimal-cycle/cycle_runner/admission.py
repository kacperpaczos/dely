"""How many disposable environments this host may hold at once, and whose they are.

The ceiling is per backend and it is one. Parallel runs are possible, but only
as a switch somebody set on purpose, with a ceiling and a budget attached; an
opt-in is not an unbounded spawn.

Two decisions make this a rail rather than a number in a file.

The count is taken while holding an exclusive lock on a file every runner
opens, so two processes starting in the same instant cannot both read "nothing
active" and both proceed.

And a lease is never reclaimed because its owner is gone. A run whose process
died before it could confirm its cleanup still holds its slot, because nobody
knows what it left behind. Clearing it is a separate command that looks for the
processes first. Everything uncertain — an unreadable lease, a `/proc` that
will not answer, a lock that will not come — occupies the slot rather than
freeing it.

A lease with no live owner also says what that run left on disk. The two facts
belong together: the lease is where an operator meets a dead run, and "nobody
knows what it left behind" is worth more when it names a private key than when
it is an adjective. A held lease is not surveyed — that run is still using its
state, and the survey walks a tree.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import residue
from .probe import digest
from .proc import utc_now

#: The default ceiling, and the only one a sequential configuration may have.
SEQUENTIAL_MAX_ACTIVE = 1

#: How long acquire waits for another runner to finish its own admission.
LOCK_WAIT_SECONDS = 20.0

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

HELD = "held"
RETAINED = "retained"
ORPHANED = "orphaned"
UNREADABLE = "unreadable"

#: Every state that occupies a slot. Each of them does.
OCCUPYING = (HELD, RETAINED, ORPHANED, UNREADABLE)


class AdmissionRefused(RuntimeError):
    """This host may not start the run that asked."""

    def __init__(self, message: str, *, occupants: Sequence["LeaseRecord"] = ()):
        super().__init__(message)
        self.occupants = tuple(occupants)


@dataclass(frozen=True)
class Claim:
    """What one run asks the host for."""

    vcpus: int = 0
    memory_mb: int = 0
    pids: int = 0
    disk_bytes: int = 0
    timeout_seconds: int = 0

    def to_document(self) -> dict[str, int]:
        return {
            "vcpus": self.vcpus,
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "disk_bytes": self.disk_bytes,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "Claim":
        def number(name: str) -> int:
            value = document.get(name, 0)
            return value if isinstance(value, int) and value >= 0 else 0

        return cls(
            vcpus=number("vcpus"),
            memory_mb=number("memory_mb"),
            pids=number("pids"),
            disk_bytes=number("disk_bytes"),
            timeout_seconds=number("timeout_seconds"),
        )

    def plus(self, other: "Claim") -> "Claim":
        return Claim(
            vcpus=self.vcpus + other.vcpus,
            memory_mb=self.memory_mb + other.memory_mb,
            pids=self.pids + other.pids,
            disk_bytes=self.disk_bytes + other.disk_bytes,
            timeout_seconds=max(self.timeout_seconds, other.timeout_seconds),
        )


#: The dimensions a budget sums across the runs that are active at once, and
#: the unit each is written in.
SUMMED = (
    ("vcpus", "virtual cpus"),
    ("memory_mb", "megabytes of memory"),
    ("pids", "processes"),
    ("disk_bytes", "bytes of disk"),
)


@dataclass(frozen=True)
class Budget:
    """The ceiling every run active at once has to fit under together."""

    vcpus: int
    memory_mb: int
    pids: int
    disk_bytes: int
    timeout_seconds: int

    def to_document(self) -> dict[str, int]:
        return {
            "vcpus": self.vcpus,
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "disk_bytes": self.disk_bytes,
            "timeout_seconds": self.timeout_seconds,
        }

    def overruns(self, total: Claim) -> tuple[str, ...]:
        """Return a sentence for every dimension the total does not fit in."""
        reasons: list[str] = []
        for name, unit in SUMMED:
            asked = getattr(total, name)
            allowed = getattr(self, name)
            if asked > allowed:
                reasons.append(f"{asked} {unit} against a budget of {allowed}")
        if total.timeout_seconds > self.timeout_seconds:
            reasons.append(
                f"a deadline of {total.timeout_seconds}s against a budget of "
                f"{self.timeout_seconds}s"
            )
        return tuple(reasons)


@dataclass(frozen=True)
class Limits:
    """The admission policy a configuration declares."""

    parallel: bool = False
    max_active: int = SEQUENTIAL_MAX_ACTIVE
    budget: Budget | None = None

    @property
    def effective_max_active(self) -> int:
        """The ceiling actually applied.

        A configuration that never asked for parallel runs gets one, whatever
        else it says: the switch is what raises the ceiling, not the number.
        """
        return self.max_active if self.parallel else SEQUENTIAL_MAX_ACTIVE

    def to_document(self) -> dict[str, Any]:
        """The policy as it was declared, so a configuration round-trips."""
        return {
            "parallel": self.parallel,
            "max_active": self.max_active,
            "budget": self.budget.to_document() if self.budget else None,
        }

    def to_record(self) -> dict[str, Any]:
        """The policy as it was applied, which is what a manifest should say."""
        return {**self.to_document(), "effective_max_active": self.effective_max_active}


@dataclass(frozen=True)
class LeaseRecord:
    """One slot, as it is on disk."""

    run_id: str
    backend: str
    path: Path
    state: str = UNREADABLE
    pid: int = 0
    process_signature: str = ""
    boot_digest: str = ""
    acquired_at: str = ""
    reason: str = ""
    claim: Claim = field(default_factory=Claim)
    #: What this run left in its own state directory, for a lease with no live
    #: owner. `None` where the question was not put: a held lease is a run
    #: still using its state, not a run that left it.
    state_residue: residue.StateResidue | None = None

    @property
    def occupies(self) -> bool:
        return self.state in OCCUPYING

    def describe(self) -> str:
        if self.state == HELD:
            return f"{self.run_id} is running as pid {self.pid}"
        if self.state == RETAINED:
            said = (
                f"{self.run_id} finished without confirming its cleanup: "
                f"{self.reason}"
            )
        elif self.state == ORPHANED:
            said = (
                f"{self.run_id} left a lease behind with no live owner; what it "
                "created was never confirmed gone"
            )
        else:
            said = f"{self.run_id} has a lease this runner cannot read: {self.reason}"
        if self.state_residue is not None and self.state_residue.present:
            said += f"; {self.state_residue.headline()}"
        return said

    def to_document(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "state": self.state,
            "pid": self.pid,
            "acquired_at": self.acquired_at,
            "reason": self.reason,
            "claim": self.claim.to_document(),
            "occupies": self.occupies,
            "state_residue": (
                self.state_residue.to_document()
                if self.state_residue is not None
                else None
            ),
        }


@dataclass
class Lease:
    """A slot this process holds."""

    record: LeaseRecord

    @property
    def run_id(self) -> str:
        return self.record.run_id

    @property
    def path(self) -> Path:
        return self.record.path


# -- the facts admission reads about this machine -------------------------


def read_boot_digest(path: str = BOOT_ID_PATH) -> str:
    """Return a digest of this boot, or the empty string when unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.readline().strip()
    except OSError:
        return ""
    return digest(value) if value else ""


def process_signature(pid: int) -> str:
    """Return something that identifies this pid *and* this incarnation of it.

    A pid on its own is reused. The start time from the process table is what
    tells a live owner from a number some later program happens to carry.
    """
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return ""
    close = raw.rfind(")")
    if close < 0:
        return ""
    fields = raw[close + 2 :].split()
    # Field 22 overall is the start time; the first two are the pid and comm.
    if len(fields) < 20:
        return ""
    return fields[19]


# -- reading the slots ----------------------------------------------------


def lease_directory(root: Path, backend: str) -> Path:
    return Path(root) / "leases" / backend


def _lock_path(root: Path, backend: str) -> Path:
    return Path(root) / "leases" / f"{backend}.lock"


def _classify(
    document: Mapping[str, Any],
    *,
    boot_digest: str,
    prober: Callable[[int], str],
) -> str:
    state = document.get("state")
    if state == RETAINED:
        return RETAINED
    pid = document.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return ORPHANED
    if boot_digest and document.get("boot_digest") and document["boot_digest"] != boot_digest:
        # The machine has rebooted since. Nothing of that run is running, but
        # nothing confirmed what it left on disk either.
        return ORPHANED
    signature = prober(pid)
    if not signature or signature != document.get("process_signature"):
        return ORPHANED
    return HELD


def read_leases(
    root: Path,
    backend: str,
    *,
    boot_digest: str | None = None,
    prober: Callable[[int], str] = process_signature,
) -> list[LeaseRecord]:
    """Return every lease for this backend, each classified as it is now."""
    directory = lease_directory(root, backend)
    if not directory.is_dir():
        return []
    boot = read_boot_digest() if boot_digest is None else boot_digest
    records: list[LeaseRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            records.append(
                LeaseRecord(
                    run_id=path.stem,
                    backend=backend,
                    path=path,
                    state=UNREADABLE,
                    reason=f"{type(error).__name__}: {error}",
                )
            )
            continue
        if not isinstance(document, Mapping):
            records.append(
                LeaseRecord(
                    run_id=path.stem,
                    backend=backend,
                    path=path,
                    state=UNREADABLE,
                    reason="the lease is not a mapping",
                )
            )
            continue
        records.append(
            LeaseRecord(
                run_id=str(document.get("run_id") or path.stem),
                backend=backend,
                path=path,
                state=_classify(document, boot_digest=boot, prober=prober),
                pid=document.get("pid") if isinstance(document.get("pid"), int) else 0,
                process_signature=str(document.get("process_signature") or ""),
                boot_digest=str(document.get("boot_digest") or ""),
                acquired_at=str(document.get("acquired_at") or ""),
                reason=str(document.get("reason") or ""),
                claim=Claim.from_document(document.get("claim") or {}),
            )
        )
    # A lease with no live owner is asked what that run left on disk. A held
    # one is not: its state is the running environment's, and the survey walks
    # a tree that a live run is still writing to.
    return [
        record
        if record.state == HELD
        else replace(record, state_residue=residue.survey(root, record.run_id))
        for record in records
    ]


# -- taking a slot --------------------------------------------------------


class _Guard:
    """An exclusive lock on the admission decision for one backend."""

    def __init__(self, path: Path, *, wait_seconds: float, sleeper: Callable[[float], None]):
        self.path = path
        self.wait_seconds = wait_seconds
        self.sleeper = sleeper
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = open(self.path, "a+", encoding="utf-8")
        except OSError as error:
            raise AdmissionRefused(
                f"the admission lock at {self.path} cannot be opened ({error}); "
                "with no way to count what is running, this host admits nothing"
            ) from error
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    self.handle.close()
                    raise AdmissionRefused(
                        f"the admission lock at {self.path} cannot be taken ({error}); "
                        "this host admits nothing it cannot count"
                    ) from error
            if time.monotonic() >= deadline:
                self.handle.close()
                raise AdmissionRefused(
                    "another runner has held the admission lock for this backend "
                    f"longer than {self.wait_seconds:g}s; this run does not start "
                    "beside a decision it cannot see"
                )
            self.sleeper(0.2)

    def __exit__(self, *exception):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


def acquire(
    *,
    root: Path,
    run_id: str,
    backend: str,
    limits: Limits,
    claim: Claim,
    pid: int | None = None,
    boot_digest: str | None = None,
    prober: Callable[[int], str] = process_signature,
    wait_seconds: float = LOCK_WAIT_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> Lease:
    """Take one slot for this run, or refuse and say who holds the others."""
    root = Path(root)
    owner = os.getpid() if pid is None else pid
    boot = read_boot_digest() if boot_digest is None else boot_digest
    directory = lease_directory(root, backend)

    with _Guard(_lock_path(root, backend), wait_seconds=wait_seconds, sleeper=sleeper):
        directory.mkdir(parents=True, exist_ok=True)
        existing = read_leases(root, backend, boot_digest=boot, prober=prober)
        occupants = [record for record in existing if record.occupies]

        if any(record.run_id == run_id for record in existing):
            raise AdmissionRefused(
                f"{run_id} already holds a lease on the {backend} backend; a run "
                "identifier is used once",
                occupants=occupants,
            )

        ceiling = limits.effective_max_active
        if len(occupants) + 1 > ceiling:
            detail = "; ".join(record.describe() for record in occupants)
            raise AdmissionRefused(
                f"the {backend} backend allows {ceiling} active environment(s) and "
                f"{len(occupants)} slot(s) are occupied: {detail}",
                occupants=occupants,
            )

        if limits.budget is not None:
            total = claim
            for record in occupants:
                total = total.plus(record.claim)
            overruns = limits.budget.overruns(total)
            if overruns:
                raise AdmissionRefused(
                    f"the {backend} backend would be asked for "
                    + "; ".join(overruns),
                    occupants=occupants,
                )
        elif limits.parallel:
            raise AdmissionRefused(
                "parallel runs are switched on without a budget; an opt-in that "
                "names no ceiling for cpu, memory, processes, disk and time is an "
                "unbounded spawn",
                occupants=occupants,
            )

        document = {
            "run_id": run_id,
            "backend": backend,
            "state": HELD,
            "pid": owner,
            "process_signature": prober(owner),
            "boot_digest": boot,
            "acquired_at": utc_now(),
            "reason": "",
            "claim": claim.to_document(),
            "limits": limits.to_document(),
        }
        path = directory / f"{run_id}.json"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as error:
            raise AdmissionRefused(
                f"the lease for {run_id} could not be created ({error})",
                occupants=occupants,
            ) from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")

    return Lease(
        record=LeaseRecord(
            run_id=run_id,
            backend=backend,
            path=path,
            state=HELD,
            pid=owner,
            process_signature=str(document["process_signature"]),
            boot_digest=boot,
            acquired_at=str(document["acquired_at"]),
            claim=claim,
        )
    )


def release(lease: Lease, *, confirmed: bool, reason: str) -> LeaseRecord:
    """Give the slot back, but only when the run confirmed it left nothing.

    An unconfirmed cleanup keeps the slot. That is the whole point: the next
    run is refused until somebody has looked at what this one left.
    """
    path = Path(lease.path)
    if confirmed:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return replace(lease.record, state="released", reason=reason)
    record = replace(lease.record, state=RETAINED, reason=reason)
    document = {
        "run_id": record.run_id,
        "backend": record.backend,
        "state": RETAINED,
        "pid": record.pid,
        "process_signature": record.process_signature,
        "boot_digest": record.boot_digest,
        "acquired_at": record.acquired_at,
        "reason": reason,
        "claim": record.claim.to_document(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # The slot stays occupied either way: a lease that cannot be rewritten
        # is still a lease on disk, and an absent one reads as orphaned.
        pass
    return record


def clear(
    *,
    root: Path,
    backend: str,
    run_id: str,
    holders: Sequence[Any] = (),
) -> LeaseRecord:
    """Remove one lease, after somebody has established nothing of it is left.

    `holders` is what a process survey found still holding this run's paths.
    Anything at all there refuses: the slot exists to stop exactly that run
    from being forgotten.
    """
    path = lease_directory(root, backend) / f"{run_id}.json"
    if not path.is_file():
        raise AdmissionRefused(f"no lease for {run_id} on the {backend} backend")
    if holders:
        listed = ", ".join(str(holder) for holder in holders)
        raise AdmissionRefused(
            f"{run_id} still has processes on this host ({listed}); the lease stays "
            "until they are gone"
        )
    record = LeaseRecord(run_id=run_id, backend=backend, path=path, state="released")
    path.unlink()
    return record
