"""What Control still owes when a worker settles, and how that debt is paid.

A valid `worker_done` settles the Task and the Dispatch by itself. It does not
close the terminal the plane opened for that agent, and the plane says so in as
many words: `orca orchestration worker-list --help` reports that terminal state
is process accounting, kept separately from Task status, and that a completed
Task can still own a live terminal. So after an accepted report the coordinator
owes each settled worker exactly one decision — reuse that terminal for a
follow-up dispatch, `worker-retain` it for somebody who is going to read it, or
`worker-release` it — and the turn is not over until every one of them has had
one.

This runner used to make none of those decisions. It left both agent terminals
`reclaimable` and destroyed the whole environment on top of them. That works,
and it is not the contract: a box going away is not a disposition, and nothing
about it is visible to the plane that is still counting.

**Release is the decision, for both workers.** Reuse is the one option the
review phase cannot take. A reviewer inheriting the implementer's terminal
inherits its session and its context, and the independence that the entire
handoff exists to establish would be gone before the reviewer read a line;
`review.py` spends a whole module proving the two dispatches were two, and
handing the second one the first one's terminal would make that proof about
nothing. Retain is for a person who is going to open the terminal afterwards,
and nobody here is: the run exports the replies and then destroys the machine
they were typed on.

**The plane is asked who is owed, rather than told.** This runner's own record
of how a worker settled is a claim about a message; terminal state is separate
accounting that the plane keeps and answers for. So the list comes from
`worker-list --run <run> --terminal-state reclaimable`, and every row it names
is released by the dispatch identifier in that row. `worker-release` is
addressed by dispatch and not by handle for a reason worth keeping: it closes
only the exact coordinator-owned agent terminal of that worker, and refuses
setup terminals, configured tabs, reused or pre-existing terminals, and any a
person has taken over. A handle would carry none of that.

**The same question is asked again afterwards.** An answer that is not empty is
a coordinator ending its turn in debt, and it is reported with the handles it
still owes rather than smoothed over.

**The terminals are listed either side of the release.** `orca terminal list`
is the live list: a released terminal is not in it and a terminal whose panel is
merely not on screen is. That comparison is the only thing here that tells a
panel disappearing from a terminal that still exists, which is a question this
project has had open since a run showed a pane vanish and proved nothing by it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .result import PhaseStatus, TerminalsRecord

#: The accounting state of a terminal whose worker has settled and which
#: nobody has claimed. `--terminal-state` also takes active, retained,
#: release_pending, release_unknown and released; this is the only one that
#: means a decision is outstanding.
RECLAIMABLE = "reclaimable"

#: The answers `worker-release` gives that discharge the decision. The plane's
#: own note is the authority: only `release_unknown` exits non-zero, and
#: `retained` here is the answer for a dispatch that never owned a terminal to
#: begin with.
#:
#: `released` is the plain success and was missing from this tuple until a real
#: run produced one. The plane's receipt schema enumerates five states —
#: released, already_released, retained, release_pending, release_unknown — and
#: a supervised worker started with `worker-start` owns a real agent terminal,
#: so closing it answers `released` with `processAction: closed_agent_terminal`.
#: The command's own note lists the *idempotent* and *no-op* answers as exiting
#: zero and says only `release_unknown` exits one; reading that list as the
#: whole set of discharges left the one answer that means the terminal is
#: actually gone being judged a failure.
DISCHARGED = ("released", "retained", "release_pending", "already_released")

#: The answer that means the plane cannot say what happened.
RELEASE_UNKNOWN = "release_unknown"

#: What Control decided to do. Only one of them is taken here, and the module
#: docstring says why the other two are not.
RELEASE = "release"


# -- the commands -----------------------------------------------------------


def owed_argv(run_id: str) -> list[str]:
    """Return the command that enumerates the terminals still owing a decision."""
    return [
        "orca",
        "orchestration",
        "worker-list",
        "--run",
        run_id,
        "--terminal-state",
        RECLAIMABLE,
        "--json",
    ]


def workers_argv(run_id: str) -> list[str]:
    """Return the command that lists this Run's workers in every terminal state.

    Unfiltered, so the record carries what the accounting said either side of
    the release rather than only the rows that matched one question.
    """
    return ["orca", "orchestration", "worker-list", "--run", run_id, "--json"]


def release_argv(dispatch_id: str) -> list[str]:
    """Return the command that releases one settled worker's own terminal."""
    return [
        "orca",
        "orchestration",
        "worker-release",
        "--dispatch",
        dispatch_id,
        "--json",
    ]


def live_argv() -> list[str]:
    """Return the command that lists the terminals that still exist."""
    return ["orca", "terminal", "list", "--json"]


# -- reading what came back -------------------------------------------------


def _document(text: str) -> dict[str, Any]:
    for candidate in (text, *text.splitlines()):
        stripped = candidate.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _result(document: Mapping[str, Any]) -> Mapping[str, Any]:
    found = document.get("result")
    return found if isinstance(found, Mapping) else {}


@dataclass(frozen=True)
class WorkerRow:
    """One row of the plane's own terminal accounting."""

    dispatch_id: str
    terminal: str = ""
    terminal_state: str = ""
    task_id: str = ""
    run_id: str = ""

    def to_document(self) -> dict[str, str]:
        return {
            "dispatch_id": self.dispatch_id,
            "terminal": self.terminal,
            "terminal_state": self.terminal_state,
            "task_id": self.task_id,
            "run_id": self.run_id,
        }


def parse_workers(stdout: str) -> list[WorkerRow]:
    """Return the worker rows a `worker-list` reply carried.

    A row with no dispatch identifier is dropped: nothing can be released by it
    and nothing can be said about it, so carrying it would only pad the record.
    """
    rows: list[WorkerRow] = []
    listed = _result(_document(stdout)).get("workers")
    if not isinstance(listed, list):
        return rows
    for entry in listed:
        if not isinstance(entry, Mapping):
            continue
        dispatch = entry.get("dispatchId")
        if not isinstance(dispatch, str) or not dispatch:
            continue
        rows.append(
            WorkerRow(
                dispatch_id=dispatch,
                terminal=str(entry.get("agentTerminalHandle") or ""),
                terminal_state=str(entry.get("terminalState") or ""),
                task_id=str(entry.get("taskId") or ""),
                run_id=str(entry.get("runId") or ""),
            )
        )
    return rows


def parse_live(stdout: str) -> list[str]:
    """Return the handles of the terminals that still exist."""
    handles: list[str] = []
    listed = _result(_document(stdout)).get("terminals")
    if not isinstance(listed, list):
        return handles
    for entry in listed:
        if isinstance(entry, Mapping) and isinstance(entry.get("handle"), str):
            if entry["handle"]:
                handles.append(entry["handle"])
    return handles


def release_outcome(stdout: str) -> str:
    """Return what `worker-release` said it did, from its own reply.

    The outcome sits in the reply's `result`, beside the dispatch it was asked
    about. An exit code alone cannot tell `already_released` from a terminal
    that was closed by this call, and the record wants the difference.
    """
    result = _result(_document(stdout))
    for key in ("outcome", "releaseOutcome", "state"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


# -- judgement, which needs no machine --------------------------------------


@dataclass(frozen=True)
class Disposition:
    """One decision Control made about one terminal, and what came back."""

    role: str
    dispatch_id: str
    terminal: str = ""
    action: str = RELEASE
    outcome: str = ""
    ok: bool = False
    detail: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "dispatch_id": self.dispatch_id,
            "terminal": self.terminal,
            "action": self.action,
            "outcome": self.outcome,
            "ok": self.ok,
            "detail": self.detail,
        }


def judge_release(
    *, role: str, row: WorkerRow, outcome: str, failure: str = ""
) -> Disposition:
    """Say whether one release discharged the decision this worker was owed."""
    disposition = Disposition(
        role=role,
        dispatch_id=row.dispatch_id,
        terminal=row.terminal,
        outcome=outcome,
    )
    if failure:
        return replace(disposition, detail=failure)
    if outcome in DISCHARGED:
        return replace(
            disposition,
            ok=True,
            detail=(
                f"the terminal of the {role}'s dispatch was released and the "
                f"plane answered {outcome}"
            ),
        )
    if outcome == RELEASE_UNKNOWN:
        return replace(
            disposition,
            detail=(
                "the plane could not say what happened to this terminal, so "
                "nothing here knows whether it was closed; the decision is "
                "still outstanding"
            ),
        )
    return replace(
        disposition,
        detail=(
            f"worker-release answered {outcome or 'nothing at all'}, which is "
            "not one of the outcomes that discharge a decision "
            f"({', '.join(DISCHARGED)})"
        ),
    )


def judge_debt(rows: Sequence[WorkerRow]) -> tuple[bool, str]:
    """Say whether Control may end its turn, from what the plane still lists."""
    if not rows:
        return True, (
            "the plane lists no terminal of this Run still owing a decision, so "
            "the coordinator ends its turn owing nothing"
        )
    named = ", ".join(
        f"{row.terminal or '<unnamed terminal>'} (dispatch {row.dispatch_id})"
        for row in rows
    )
    return False, (
        f"the coordinator is ending its turn still owing a decision on "
        f"{len(rows)} terminal(s): {named}"
    )


def distinguishes(
    *, before: Sequence[str], after: Sequence[str], released: Sequence[str]
) -> tuple[bool, str]:
    """Say whether a released terminal is distinguishable from a hidden one.

    A handle that was in the live list before the release and is not in it
    after is a terminal that stopped existing. One that is still there was not
    closed, whatever its panel did and whatever the release receipt claimed.
    This answers an open question and gates nothing: it is the measurement the
    release makes possible, not a promise the run made.
    """
    live_before = set(before)
    live_after = set(after)
    named = [handle for handle in released if handle]
    if not named:
        return False, (
            "no terminal was released, so there is nothing here to tell a "
            "closed terminal apart from a hidden one"
        )
    unlisted = [handle for handle in named if handle not in live_before]
    if unlisted:
        return False, (
            "these terminals were released but were not in the live list "
            "beforehand, so their leaving it establishes nothing: "
            + ", ".join(unlisted)
        )
    surviving = [handle for handle in named if handle in live_after]
    if surviving:
        return False, (
            "these terminals were released and are still in the live list, so "
            "the release did not close them: " + ", ".join(surviving)
        )
    return True, (
        f"all {len(named)} released terminal(s) were in the live list before "
        f"the release and are not in it after, of {len(live_after)} still "
        "there; a terminal whose panel is merely off screen stays in this list, "
        "so this is a closure rather than a disappearance"
    )


@dataclass(frozen=True)
class Snapshot:
    """The terminals that exist, and the plane's accounting for this Run."""

    live: tuple[str, ...] = ()
    workers: tuple[WorkerRow, ...] = ()


def record(
    *,
    run_id: str,
    before: Snapshot,
    after: Snapshot,
    owed_before: Sequence[WorkerRow],
    owed_after: Sequence[WorkerRow],
    dispositions: Sequence[Disposition],
) -> TerminalsRecord:
    """Turn one disposition round into the record the run exports."""
    clean, why = judge_debt(owed_after)
    unresolved = [item for item in dispositions if not item.ok]
    distinguished, how = distinguishes(
        before=before.live,
        after=after.live,
        released=[item.terminal for item in dispositions if item.ok],
    )
    if clean and not unresolved:
        detail = (
            f"{len(dispositions)} terminal(s) this Run's dispatches owned were "
            f"released rather than reused or left to the environment's "
            f"destruction; {why}"
            if dispositions
            # A worker that never settled owns a terminal that is still active,
            # and an active terminal owes nobody a decision yet. Saying nothing
            # was released is the honest answer, not a clean sweep.
            else f"no terminal of this Run was owed a decision; {why}"
        )
        status = PhaseStatus.OK
    else:
        problems = [why] if not clean else []
        problems.extend(f"{item.role}: {item.detail}" for item in unresolved)
        detail = "; ".join(problems)
        status = PhaseStatus.FAILED
    return TerminalsRecord(
        status=status,
        run_id=run_id,
        owed_before=[row.to_document() for row in owed_before],
        dispositions=[item.to_document() for item in dispositions],
        owed_after=[row.to_document() for row in owed_after],
        live_before=list(before.live),
        live_after=list(after.live),
        workers_before=[row.to_document() for row in before.workers],
        workers_after=[row.to_document() for row in after.workers],
        clean=clean,
        distinguished=distinguished,
        distinction_detail=how,
        detail=detail,
    )
