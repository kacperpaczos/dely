"""A backend adapter that records what the lifecycle asked it to do.

It is a real implementation of the interface over a host directory, not a
mock: the lifecycle's ordering claims are checked against what actually
happened to files, and the recorded call list is what proves the order.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Mapping, Sequence

import hashlib
import json

from cycle_runner import (
    display,
    firstrun,
    probe,
    proc,
    review,
    screenshot,
    skills,
    toolchain,
)
from cycle_runner.adapters.base import (
    BackendAdapter,
    DestroyReport,
    EnvironmentHandle,
    Finding,
    PreflightReport,
    Resource,
    StopReport,
)

HOST = {
    "hostname": "workshop",
    "machine_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "boot_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "home": "/home/someone",
    "user": "someone",
    "path": "/usr/bin:/bin",
    "pid": "1",
    "uname": "linux workshop amd64",
    "container_marker": "",
    "virt": "none",
    "orca_path": "/usr/bin/orca",
    "orca_version": "1.4.201",
    "project_real": "/home/someone/code/under-test",
}


#: The only programs a fake environment may really run on this machine, and
#: the only `sh -c` scripts among them. `orca-start` is deliberately absent.
RUNNABLE_PROGRAMS = frozenset(
    {"sh", "rm", "mkdir", "cat", "test", "true", "false", "printf", "git"}
)
#: `cycle-screenshot` is deliberately absent for the same reason as
#: `orca-start`: the real script runs `xset` and `xwd` against whatever screen
#: it is given, and on this machine that is somebody's desktop. The fake answers
#: it before the allowlist is consulted; the allowlist is what catches the day
#: that answer stops matching.
RUNNABLE_SHELL_SCRIPTS = frozenset(
    {
        "cycle-check",
        "auth-teardown",
        "cycle-cd",
        "cycle-handoff",
        "cycle-skills",
        "cycle-display",
        "auth-diagnosis",
    }
)

ORCA_STATUS_REPLY = (
    '{"ok": true, "result": {"app": {"running": true, "pid": 1786, '
    '"desktopWindowStatus": "available"}, "runtime": {"state": "ready", '
    '"reachable": true, "connectionState": "connected", "runtimeId": '
    '"runtime-fake", "appVersion": "1.4.201", "capabilities": '
    '["orchestration.contract.v1"]}}}'
)

ORCA_STATUS_DOWN = (
    '{"ok": true, "result": {"app": {"running": false, "pid": null}, '
    '"runtime": {"state": "unavailable", "reachable": false}}}'
)

ORCA_TERMINAL_REPLY = (
    '{"ok": true, "result": {"terminal": {"handle": "term_fake", '
    '"worktreeId": "repo::project", "surface": "visible"}}}'
)

# The real shape: identifiers and the delivery both nested under `result`, the
# top-level `id` being the request's. One reply serves all three orchestration
# commands here.
ORCA_DISPATCH_REPLY = (
    '{"id": "request-fake", "ok": true, "result": {"run": {"id": "run-fake"}, '
    '"runId": "run-fake", "taskId": "task-fake", "dispatchId": "dispatch-fake", '
    '"state": "ready", '
    '"effects": [{"kind": "terminal", "role": "agent", "id": "terminal-fake"}], '
    '"messages": [{"type": "worker_done", '
    '"payload": "{\\"outcome\\": \\"DONE\\"}"}], "count": 1, '
    '"deliveryId": "delivery-fake"}}'
)

#: A wait that woke with nothing to report, in the same shape.
ORCA_EMPTY_DELIVERY = (
    '{"id": "request-fake", "ok": true, "result": {"messages": [], "count": 0}}'
)

#: What the runtime answers when it is asked to bring a terminal's panel to
#: the front: the handle it moved to, the tab it belongs to, and whether the
#: window actually navigated. The last is the field worth faking separately,
#: because a switch that exits zero having moved nothing is how this fails in
#: a real environment and it is indistinguishable from success by exit code.
ORCA_SWITCH_REPLY = (
    '{"ok": true, "result": {"focus": {"handle": "%s", "tabId": "tab-fake", '
    '"worktreeId": "repo::project", "navigated": %s}}}'
)

#: The handle of the coordinator's own terminal. It is never a worker row and
#: nothing here may ever release it: `worker-release` closes only the agent
#: terminal a dispatch owns, and a fake that forgot that would let the runner
#: look correct while closing the terminal it sends from.
COORDINATOR_HANDLE = "term_fake"

#: What a fake screen looks like: not an image, but bytes that travel and hash
#: like one, so the digest and the re-read the runner does are really exercised.
SCREEN_FRAME = b"\x89PNG\r\n\x1a\n fake frame of the environment's own screen"


def render(snapshot: Mapping[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in snapshot.items())


class FakeAdapter(BackendAdapter):
    """An environment that is really just a directory on the host."""

    name = "fake"

    def __init__(
        self,
        root: Path,
        *,
        preflight_ok: bool = True,
        identity: str = "environment",
        orca_present: bool = True,
        check_exit_code: int = 0,
        task_hangs: bool = False,
        fetch_fails: bool = False,
        stop_confirmed: bool = True,
        leave_residue: bool = False,
        marker: str = "dely-cycle-marker",
        orca_path_from_host: bool = False,
        runtime_ready: bool = True,
        terminal_refused: bool = False,
        terminal_answers: str | None = None,
        already_running: bool = False,
        refuse_repository: bool = False,
        dispatch_state: str | None = None,
        wait_settles: bool = True,
        create_fails: bool = False,
        task_writes_nothing: bool = False,
        host_project: Path | None = None,
        skill_answers: Mapping[str, tuple[str, str]] | None = None,
        review_verdict: str | None = "accept",
        reviewer_reads_another_diff: bool = False,
        one_dispatch_for_both: bool = False,
        display_unreachable: bool = False,
        signed_in: bool | None = True,
        window_appears: bool = True,
        capture_fails: bool = False,
        capture_vanishes: bool = False,
        captures_from_outside: bool = False,
        plugin_answers: Mapping[str, tuple[str, str]] | None = None,
        plugin_installed: Mapping[str, tuple[int, int]] | None = None,
        release_fails: bool = False,
        release_leaves_the_terminal: bool = False,
        still_owed: bool = False,
        panel_switch_refused: bool = False,
        panel_never_moves: bool = False,
        toolchain_binary: str = "/usr/local/bin/claude",
        toolchain_version: str = "pinned-by-the-operator",
        toolchain_shadow: str = "",
        agent_process_gone: bool = False,
    ):
        self.root = Path(root)
        self.skill_answers = dict(skill_answers or {})
        self.dispatches = 0
        # The plane's own terminal accounting, keyed by dispatch: one agent
        # terminal per dispatch, its state, and whether it still exists.
        self.worker_terminals: dict[str, dict[str, object]] = {}
        self.release_fails = release_fails
        # The two ways asking for a panel goes wrong: the runtime refuses the
        # command outright, and the runtime accepts it and the window stays
        # where it was.
        self.panel_switch_refused = panel_switch_refused
        self.panel_never_moves = panel_never_moves
        # What a bare `claude` finds in this environment, what it says it is,
        # and what else the same search path could have reached. The shadow is
        # the whole point of the probe: an environment with only one candidate
        # answers the same whether the ordering was ever fixed.
        self.toolchain_binary = toolchain_binary
        self.toolchain_version = toolchain_version
        self.toolchain_shadow = toolchain_shadow
        self.agent_process_gone = agent_process_gone
        self.release_leaves_the_terminal = release_leaves_the_terminal
        self.still_owed = still_owed
        self.unacknowledged: str | None = None
        self.review_verdict = review_verdict
        self.reviewer_reads_another_diff = reviewer_reads_another_diff
        self.one_dispatch_for_both = one_dispatch_for_both
        self.display_unreachable = display_unreachable
        self.signed_in = signed_in
        self.window_appears = window_appears
        self.capture_fails = capture_fails
        self.capture_vanishes = capture_vanishes
        # A backend that can be asked for its screen from outside itself, which
        # is what the machine backend's hypervisor route is.
        self.screen_capture_format = "ppm" if captures_from_outside else ""
        self.plugin_answers = dict(plugin_answers or {})
        self.plugin_installed = dict(plugin_installed or {})
        self.home = self.root / "home"
        self.project = self.home / "project"
        self.shared_base = self.root.parent / "shared" / "base.img"
        self.calls: list[str] = []
        # What the environment's Orca profile held at the moment the
        # application was started, read here because it cannot be read
        # afterwards: the same file written a second later looks identical
        # once the run is over, and only one of the two answers the wizard.
        self.profile_at_start: str | None = None
        self.preflight_ok = preflight_ok
        self.identity = identity
        self.orca_present = orca_present
        self.check_exit_code = check_exit_code
        self.task_hangs = task_hangs
        self.fetch_fails = fetch_fails
        self.stop_confirmed = stop_confirmed
        self.leave_residue = leave_residue
        self.marker = marker
        self.orca_path_from_host = orca_path_from_host
        self.runtime_ready = runtime_ready
        self.terminal_refused = terminal_refused
        self.terminal_answers = terminal_answers
        # The application is not running until something starts it, which is
        # what the real environment does too.
        self.app_started = already_running
        self.refuse_repository = refuse_repository
        self.dispatch_state = dispatch_state
        self.wait_settles = wait_settles
        self.create_fails = create_fails
        self.task_writes_nothing = task_writes_nothing
        self.host_project = host_project
        self.destroyed = False

    # -- lifecycle --------------------------------------------------------

    def preflight(self) -> PreflightReport:
        self.calls.append("preflight")
        return PreflightReport(
            backend=self.name,
            findings=(
                Finding(
                    name="fake backend",
                    ok=self.preflight_ok,
                    detail="configured by the test",
                ),
            ),
        )

    def plan_handle(self) -> EnvironmentHandle:
        return EnvironmentHandle(
            environment_id=f"fake-{self.root.name}",
            home_path=str(self.home),
            project_path=str(self.project),
            per_run_resources=(Resource(kind="path", identifier=str(self.home)),),
            shared_resources=(Resource(kind="image", identifier=str(self.shared_base)),),
            description={"image": "fake"},
        )

    def create(self) -> EnvironmentHandle:
        self.calls.append("create")
        self.project.mkdir(parents=True, exist_ok=True)
        if self.create_fails:
            self.shared_base.parent.mkdir(parents=True, exist_ok=True)
            self.shared_base.write_bytes(b"shared base image")
            raise RuntimeError("the container manager refused halfway through")
        self.shared_base.parent.mkdir(parents=True, exist_ok=True)
        self.shared_base.write_bytes(b"shared base image")
        return EnvironmentHandle(
            environment_id=f"fake-{self.root.name}",
            home_path=str(self.home),
            project_path=str(self.project),
            per_run_resources=(Resource(kind="path", identifier=str(self.home)),),
            shared_resources=(Resource(kind="image", identifier=str(self.shared_base)),),
            description={"image": "fake"},
        )

    def _host_snapshot_text(self) -> str:
        target = str(self.host_project or self.project)
        return proc.run(probe.probe_argv(target), timeout=30, context="host").stdout

    def _snapshot(self) -> str:
        if self.identity == "host":
            # The counterexample: the command really did run on the host, so the
            # probe answers with the host's own values rather than a stand-in.
            return self._host_snapshot_text()
        environment = dict(HOST)
        environment.update(
            {
                "hostname": "fake-box",
                "machine_id": "cccccccccccccccccccccccccccccccc",
                "boot_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "home": str(self.home),
                "container_marker": "containerenv",
                "project_real": str(self.project),
                "orca_path": self._orca_path(),
                "orca_version": "1.4.201" if self.orca_present else "",
            }
        )
        return render(environment)

    def _orca_path(self) -> str:
        if not self.orca_present:
            return ""
        if self.orca_path_from_host:
            return probe.parse(self._host_snapshot_text()).get("orca_path", "")
        return "/usr/bin/orca"

    def _windows(self, argv) -> str:
        """Answer as the environment's own screen would.

        A window for the application appears only once it has been started, so
        the before-and-after comparison the runner makes is a real one here.
        """
        screen = argv[4] if len(argv) > 4 else ":0"
        if self.display_unreachable:
            return f"display {screen} unreachable\n"
        lines = [f"display {screen} reachable", "window 1000 openbox"]
        if self.app_started and self.window_appears:
            lines.append("window 2000 orca — project")
        return "\n".join(lines) + "\n"

    def _screenshot(self, argv) -> str:
        """Answer as the environment's own X server would, and leave the file.

        The real script runs `xwd` against the screen it is given. Running that
        here would photograph the desktop of whoever is running the tests, so
        this writes the bytes instead and reports them exactly as the script
        does — the runner still has to fetch them and re-read what landed.
        """
        screen = argv[4] if len(argv) > 4 else ":0"
        directory = Path(argv[5]) if len(argv) > 5 else self.home / "screenshots"
        if self.display_unreachable:
            return f"screen {screen} not captured\nreason the screen did not answer\n"
        if self.capture_fails:
            return (
                f"screen {screen} not captured\n"
                "reason xwd could not read the root window of this screen\n"
            )
        directory.mkdir(parents=True, exist_ok=True)
        if not self.capture_vanishes:
            # The other way a capture goes wrong: the command reports an image
            # and the host ends up with nothing, because the transport brought
            # out an empty directory or the file was gone by then.
            (directory / "screen.png").write_bytes(SCREEN_FRAME)
        return (
            f"screen {screen} captured\n"
            "file screen.png\n"
            f"bytes {len(SCREEN_FRAME)}\n"
            f"sha256 {hashlib.sha256(SCREEN_FRAME).hexdigest()}\n"
        )

    def capture_screen(self, host_path: str):
        """Answer the hypervisor route without a hypervisor and without a process."""
        self.calls.append("capture-screen")
        if not self.capture_fails:
            Path(host_path).parent.mkdir(parents=True, exist_ok=True)
            Path(host_path).write_bytes(SCREEN_FRAME)
        return proc.CommandOutcome(
            argv=("virsh", "screenshot", "--file", str(host_path)),
            exit_code=1 if self.capture_fails else 0,
            stdout="" if self.capture_fails else f"Screenshot saved to {host_path}\n",
            stderr="the domain has no graphics device" if self.capture_fails else "",
            started_at="2026-09-14T22:15:30Z",
            finished_at="2026-09-14T22:15:31Z",
            elapsed_seconds=1.0,
            timed_out=False,
            # It runs on the host: that is the whole point of this route.
            context="host",
        )

    def _delivery(self, joined: str) -> str:
        """Answer a wait the way a bound Run does.

        A delivery is replayed until it is acknowledged, so a wait that does not
        acknowledge the previous batch gets that batch again — with the previous
        agent's message in it.
        """
        acknowledged = "--ack" in joined
        if acknowledged:
            self.unacknowledged = None
        if self.unacknowledged is not None:
            self.calls.append("replayed-delivery")
            return self.unacknowledged
        delivered = ORCA_DISPATCH_REPLY.replace(
            '"deliveryId": "delivery-fake"',
            f'"deliveryId": "delivery-fake-{self.dispatches}"',
        ).replace(
            '"dispatchId": "dispatch-fake"',
            f'"dispatchId": "dispatch-fake-{self.dispatches}"',
        )
        self.unacknowledged = delivered
        return delivered

    def _dispatch(self, joined: str) -> str:
        """Answer one worker-start, and do what that agent would have done.

        Each dispatch gets its own identifier and its own agent terminal,
        because that is what the runner compares to decide whether two agents
        were really two.
        """
        self.dispatches += 1
        number = 1 if self.one_dispatch_for_both else self.dispatches
        reply = ORCA_DISPATCH_REPLY.replace(
            '"dispatchId": "dispatch-fake"', f'"dispatchId": "dispatch-fake-{number}"'
        ).replace('"id": "terminal-fake"', f'"id": "terminal-fake-{number}"')
        # The plane opens a terminal for the agent and keeps counting it after
        # the Task settles: a dispatch whose reply already carries worker_done
        # is reclaimable, one whose turn start was never observed is still
        # active and owes nobody anything yet.
        self.worker_terminals[f"dispatch-fake-{number}"] = {
            "terminal": f"terminal-fake-{number}",
            "state": "active" if self.dispatch_state else "reclaimable",
            "live": True,
        }
        if review.PROMPT_NAME in joined:
            self._review()
        elif not self.task_writes_nothing:
            self.project.mkdir(parents=True, exist_ok=True)
            (self.project / "evidence.txt").write_text(self.marker, encoding="utf-8")
        return reply

    def _resolution(self) -> str:
        """Answer as the environment's own search path would."""
        lines = [f"selected={self.toolchain_binary}"]
        if self.toolchain_binary:
            lines.append(f"selected_version={self.toolchain_version}")
            lines.append(f"reachable={self.toolchain_binary}")
        if self.toolchain_shadow:
            lines.append(f"reachable={self.toolchain_shadow}")
        return "\n".join(lines) + "\n"

    def _agent_processes(self) -> str:
        """Answer as this run's own processes would, or say there are none."""
        if self.agent_process_gone or not self.toolchain_binary:
            return "examined=0\n"
        return (
            f"process={self.toolchain_binary}\t{self.toolchain_binary}\n"
            "examined=1\n"
        )

    def _review(self) -> None:
        """Write the verdict a reviewer would have written."""
        if self.review_verdict is None:
            return
        diff = self.home / review.DIFF_NAME
        digest = (
            hashlib.sha256(diff.read_bytes()).hexdigest() if diff.is_file() else ""
        )
        if self.reviewer_reads_another_diff:
            digest = hashlib.sha256(b"a different diff entirely").hexdigest()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / review.VERDICT_NAME).write_text(
            json.dumps(
                {
                    "diff_sha256": digest,
                    "verdict": self.review_verdict,
                    "reason": "the change creates the file with exactly the marker",
                }
            ),
            encoding="utf-8",
        )

    # -- terminal accounting, which a settled Task does not touch ----------

    @staticmethod
    def _flag(joined: str, name: str) -> str:
        """Return the value that followed one flag, as the real command reads it."""
        if name not in joined:
            return ""
        rest = joined.split(name, 1)[1].split()
        return rest[0] if rest else ""

    def _worker_list(self, joined: str) -> str:
        """Answer as the plane's worker accounting does, filter and all."""
        wanted = self._flag(joined, "--terminal-state")
        rows = [
            {
                "dispatchId": dispatch,
                "taskId": "task-fake",
                "runId": "run-fake",
                "agentTerminalHandle": entry["terminal"],
                "terminalState": entry["state"],
            }
            for dispatch, entry in self.worker_terminals.items()
            if not wanted or entry["state"] == wanted
        ]
        return json.dumps(
            {
                "id": "request-fake",
                "ok": True,
                "result": {
                    "workers": rows,
                    "page": {"limit": 100, "total": len(rows), "hasMore": False},
                    "scope": {"source": "flag"},
                },
            }
        )

    def _worker_release(self, joined: str) -> tuple[int, str]:
        """Release one dispatch's own terminal, and nobody else's.

        The coordinator's terminal is not in this table at all, so a call that
        named it would find nothing — which is the real verb's behaviour and
        the reason it is addressed by dispatch rather than by handle.
        """
        dispatch = self._flag(joined, "--dispatch")
        entry = self.worker_terminals.get(dispatch)
        if entry is None or self.release_fails:
            return 1, json.dumps(
                {
                    "id": "request-fake",
                    "ok": False,
                    "result": {"dispatchId": dispatch, "outcome": "release_unknown"},
                }
            )
        if self.release_leaves_the_terminal:
            # The receipt is fine and the terminal is still there: the case a
            # run that trusted the receipt could never tell from a closure.
            entry["state"] = "release_pending"
        elif self.still_owed:
            # The terminal went, and the accounting still owes a decision on it.
            entry["live"] = False
            entry["state"] = "reclaimable"
        else:
            entry["live"] = False
            entry["state"] = "released"
        return 0, json.dumps(
            {
                "id": "request-fake",
                "ok": True,
                "result": {"dispatchId": dispatch, "outcome": "release_pending"},
            }
        )

    def _terminal_list(self) -> str:
        """Answer with the terminals that still exist, coordinator included."""
        handles = [COORDINATOR_HANDLE] + [
            str(entry["terminal"])
            for entry in self.worker_terminals.values()
            if entry["live"]
        ]
        return json.dumps(
            {
                "id": "request-fake",
                "ok": True,
                "result": {
                    "terminals": [{"handle": handle} for handle in handles],
                    "totalCount": len(handles),
                },
            }
        )

    @staticmethod
    def _skill_lines(argv: Sequence[str], answers: Mapping[str, tuple[str, str]]) -> str:
        """Answer one line per name the probe was asked about, as the script does."""
        lines = []
        for item in argv[4:]:
            name = item.split("=", 1)[0]
            if name.startswith("."):  # the roots argument, not a name
                continue
            first, second = answers.get(name, ("missing", ""))
            lines.append(f"{name}\t{first}\t{second}")
        return "\n".join(lines) + ("\n" if lines else "")

    def execute(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        extra_values: Sequence[str] = (),
    ) -> proc.CommandOutcome:
        joined = " ".join(argv)
        if display.WINDOW_SCRIPT in joined:
            self.calls.append("display-windows")
            return self._outcome(argv, 0, self._windows(argv), "")
        if screenshot.CAPTURE_SCRIPT in joined:
            self.calls.append("screenshot")
            return self._outcome(argv, 0, self._screenshot(argv), "")
        if skills.LOCATE_SCRIPT in joined:
            self.calls.append("skills-locate")
            return self._outcome(argv, 0, self._skill_lines(argv, self.skill_answers), "")
        if skills.INSTALLED_SCRIPT in joined:
            self.calls.append("skills-installed")
            answers = {
                name: (str(total), str(same))
                for name, (total, same) in self.plugin_installed.items()
            }
            return self._outcome(argv, 0, self._skill_lines(argv, answers), "")
        if skills.REVISION_SCRIPT in joined:
            self.calls.append("skills-revision")
            return self._outcome(argv, 0, self._skill_lines(argv, self.plugin_answers), "")
        if probe.PROBE_SCRIPT in joined:
            self.calls.append("probe")
            return self._outcome(argv, 0, self._snapshot(), "")
        if toolchain.RESOLUTION_SCRIPT in joined:
            self.calls.append("toolchain-resolution")
            return self._outcome(argv, 0, self._resolution(), "")
        if toolchain.PROCESS_SCRIPT in joined:
            self.calls.append("toolchain-processes")
            return self._outcome(argv, 0, self._agent_processes(), "")
        # Before the dispatch branch: these are orchestration commands too, and
        # they are about the terminal the plane counts rather than the Task.
        if "worker-list" in joined:
            self.calls.append("worker-list")
            return self._outcome(argv, 0, self._worker_list(joined), "")
        if "worker-release" in joined:
            self.calls.append("worker-release")
            exit_code, reply = self._worker_release(joined)
            return self._outcome(argv, exit_code, reply, "")
        if "terminal list" in joined:
            self.calls.append("terminal-list")
            return self._outcome(argv, 0, self._terminal_list(), "")
        if "orchestration" in joined:
            self.calls.append("worker")
            if self.task_hangs:
                return self._outcome(argv, None, "", "deadline reached", timed_out=True)
            if self.dispatch_state and "worker-start" in joined:
                # Still a distinct dispatch: an unobserved turn start says
                # nothing about whether this is the same agent as the last one.
                reply = self._dispatch(joined).replace(
                    '"state": "ready"', f'"state": "{self.dispatch_state}"'
                )
                return self._outcome(argv, 1, reply, "")
            if "--wait" in joined:
                if not self.wait_settles:
                    # The dispatch never reports, so the wait returns nothing.
                    return self._outcome(argv, 0, ORCA_EMPTY_DELIVERY, "")
                return self._outcome(argv, 0, self._delivery(joined), "")
            if "worker-start" in joined:
                return self._outcome(argv, 0, self._dispatch(joined), "")
            if not self.task_writes_nothing:
                self.project.mkdir(parents=True, exist_ok=True)
                (self.project / "evidence.txt").write_text(self.marker, encoding="utf-8")
            return self._outcome(argv, 0, ORCA_DISPATCH_REPLY, "")
        if argv[0] == "git" and self.refuse_repository:
            self.calls.append("git-refused")
            return self._outcome(argv, 1, "", "the fake refuses to initialise a repository")
        if len(argv) > 3 and argv[0] == "sh" and argv[3] == "orca-start":
            # Simulated, never executed: running this would start a desktop
            # application on the machine running the tests.
            self.calls.append("orca-start")
            seed = self.home / firstrun.PROFILE_RELATIVE
            self.profile_at_start = (
                seed.read_text(encoding="utf-8") if seed.is_file() else None
            )
            self.app_started = True
            return self._outcome(argv, 0, "display ready after 0s\nstarted 1786\n", "")
        if tuple(argv[:3]) == ("claude", "auth", "status"):
            self.calls.append("auth-status")
            if self.signed_in is None:
                return self._outcome(argv, 1, "", "not a readable answer")
            return self._outcome(
                argv,
                0,
                json.dumps(
                    {
                        "loggedIn": self.signed_in,
                        "authMethod": "claude.ai",
                        "apiProvider": "firstParty",
                        # Present in the real answer and deliberately not kept.
                        "email": "somebody@example.invalid",
                        "orgName": "somebody's organisation",
                    }
                ),
                "",
            )
        if tuple(argv[:2]) == ("orca", "status"):
            self.calls.append("orca-status")
            up = self.orca_present and self.runtime_ready and self.app_started
            return self._outcome(argv, 0, ORCA_STATUS_REPLY if up else ORCA_STATUS_DOWN, "")
        if "terminal switch" in joined:
            self.calls.append("terminal-switch")
            if self.panel_switch_refused:
                return self._outcome(argv, 1, "", "terminal_exited")
            handle = self._flag(joined, "--terminal") or ""
            moved = "false" if self.panel_never_moves else "true"
            return self._outcome(argv, 0, ORCA_SWITCH_REPLY % (handle, moved), "")
        if "terminal create" in joined:
            self.calls.append("terminal-create")
            if self.terminal_refused:
                return self._outcome(argv, 1, "", "the runtime refused a terminal")
            return self._outcome(argv, 0, ORCA_TERMINAL_REPLY, "")
        if "terminal send" in joined:
            self.calls.append("terminal-send")
            return self._outcome(argv, 0, '{"ok": true}', "")
        if "terminal read" in joined:
            # The runner asks the terminal which machine it is on. This fake
            # answers as the environment unless a test asks it to answer as
            # somewhere else, which is the shape of a terminal that opened
            # outside the environment.
            self.calls.append("terminal-read")
            name = self.terminal_answers or f"fake-{self.root.name}"
            return self._outcome(
                argv, 0, f"handle: term_fake\nstatus: running\n\ncycle-terminal:{name}\n", ""
            )
        if "repo add" in joined:
            self.calls.append("repo-add")
            return self._outcome(argv, 0, '{"ok": true}', "")
        if "cycle-check" in joined:
            self.calls.append("check")
        else:
            self.calls.append(f"execute:{argv[0]}")
        refusal = self._refuse(argv)
        if refusal is not None:
            return refusal
        return proc.run(
            argv,
            timeout=timeout,
            context="environment",
            cwd=cwd,
            env=env,
            extra_values=extra_values,
        )

    def _refuse(self, argv) -> proc.CommandOutcome | None:
        """Answer, rather than run, anything outside the narrow allowlist.

        This fake's "environment" is a directory on the developer's own
        machine, so a command it does not recognise runs *here*. That is not
        hypothetical: when the runner gained a step that starts the Orca
        desktop application, every lifecycle test launched a real one on the
        host. A test double that can start an application will start it.
        """
        program = Path(argv[0]).name
        if program not in RUNNABLE_PROGRAMS:
            return self._outcome(
                argv, 127, "", f"the fake environment does not run {program}"
            )
        if program == "sh":
            name = argv[3] if len(argv) > 3 else ""
            if name not in RUNNABLE_SHELL_SCRIPTS:
                return self._outcome(
                    argv, 127, "", f"the fake environment does not run the script {name!r}"
                )
        if program == "git":
            # Only inside the fake's own root, and only in that form: `git`
            # reaches a great deal further than this fake should.
            inside = (
                len(argv) > 2
                and argv[1] == "-C"
                and Path(argv[2]).is_relative_to(self.root)
            )
            if not inside:
                return self._outcome(
                    argv, 127, "", "the fake environment runs git only inside its own root"
                )
        if program == "rm":
            targets = [item for item in argv[1:] if not item.startswith("-")]
            outside = [
                item for item in targets if not Path(item).is_relative_to(self.root)
            ]
            if outside:
                return self._outcome(
                    argv, 1, "", f"refusing to remove outside the fake root: {outside}"
                )
        return None

    def _outcome(self, argv, exit_code, stdout, stderr, timed_out=False):
        return proc.CommandOutcome(
            argv=tuple(argv),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at="2026-09-14T22:15:30Z",
            finished_at="2026-09-14T22:15:31Z",
            elapsed_seconds=1.0,
            timed_out=timed_out,
            context="environment",
        )

    def put_tree(self, local_dir: Path, remote_dir: str) -> None:
        self.calls.append("put_tree")
        destination = Path(remote_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for path in sorted(Path(local_dir).rglob("*")):
            if path.is_file():
                target = destination / path.relative_to(local_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())

    def fetch_tree(self, remote_dir: str, local_dir: Path) -> None:
        self.calls.append("fetch_tree")
        if self.fetch_fails:
            raise OSError("the transport refused to copy the tree out")
        source = Path(remote_dir)
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        for path in sorted(source.rglob("*")):
            if path.is_file():
                target = Path(local_dir) / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())

    def write_file(self, remote_path: str, content: str, *, mode: int = 0o600) -> None:
        self.calls.append("write_file")
        target = Path(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(mode)

    def stop(self) -> StopReport:
        self.calls.append("stop")
        return StopReport(
            confirmed=self.stop_confirmed,
            detail="stopped by the fake adapter"
            if self.stop_confirmed
            else "the fake adapter could not confirm the stop",
        )

    def destroy(self) -> DestroyReport:
        self.calls.append("destroy")
        if not self.leave_residue and self.home.exists():
            shutil.rmtree(self.home)
        self.destroyed = True
        return DestroyReport(
            removed=() if self.leave_residue else (str(self.home),),
            retained=(str(self.shared_base),),
            detail="fake destroy",
        )

    def resource_exists(self, resource: Resource) -> bool:
        return Path(resource.identifier).exists()

    def describe(self) -> dict:
        return {"backend": self.name, "image": "fake"}


class ScriptedAdapter(FakeAdapter):
    """A fake whose command answers are chosen by matching the argv."""

    def __init__(self, root: Path, script=None, **options):
        super().__init__(root, **options)
        self.script = list(script or [])
        self.executed: list[tuple[str, ...]] = []
        self.written: list[tuple[str, str, int]] = []

    def execute(
        self,
        argv,
        *,
        timeout: float,
        cwd=None,
        env=None,
        extra_values=(),
    ):
        argv = tuple(argv)
        self.executed.append(argv)
        joined = " ".join(argv)
        for needle, exit_code, stdout, stderr, timed_out in self.script:
            if needle in joined:
                return self._outcome(argv, exit_code, stdout, stderr, timed_out)
        return self._outcome(argv, 0, "", "")

    def write_file(self, remote_path: str, content: str, *, mode: int = 0o600) -> None:
        self.written.append((remote_path, content, mode))
        super().write_file(remote_path, content, mode=mode)
