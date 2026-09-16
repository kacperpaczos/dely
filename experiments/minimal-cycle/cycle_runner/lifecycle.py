"""The shared cycle: one lifecycle, whichever backend is behind it.

Two orderings carry the contract. Evidence is collected and exported before
anything is stopped or destroyed, so no terminal state loses the run. And the
task runs only after the identity gate has shown that the environment answers
as itself, so a missing Orca is a blocked run rather than a quiet fall back
onto the host's installation.
"""

from __future__ import annotations

import json
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from . import (
    admission,
    auth,
    cleanup,
    display as display_module,
    firstrun,
    hostinfo,
    hostregistry,
    manifest,
    orca,
    probe,
    proc,
    processes,
    project,
    redact,
    review as review_module,
    skills,
    worker,
)
from .adapters.base import BackendAdapter, EnvironmentHandle
from .config import RunConfig
from .export import Exporter
from .result import (
    AdmissionRecord,
    DisplayRecord,
    CheckRecord,
    CleanupRecord,
    PhaseRecord,
    RunResult,
)
from .status import CleanupStatus, ExportStatus, PhaseStatus, RunStatus, exit_code

PHASE_ORDER = (
    "prepare",
    "create",
    "bootstrap",
    "identity",
    "task",
    "review",
    "check",
    "collect",
    "export",
    "cleanup",
    "close",
)

CHECK_SCRIPT = r"""
set -u
target="$1"
expected="$2"
if [ ! -f "$target" ]; then
    printf 'missing: %s\n' "$target" >&2
    exit 3
fi
actual="$(cat "$target")"
if [ "$actual" = "$expected" ]; then
    printf 'marker matched at %s\n' "$target"
    exit 0
fi
printf 'marker mismatch at %s\n' "$target" >&2
exit 4
"""


def check_argv(project_path: str, relative_path: str, marker: str) -> list[str]:
    """Return the independent check as one command the environment can run."""
    return [
        "sh",
        "-c",
        CHECK_SCRIPT,
        "cycle-check",
        str(Path(project_path) / relative_path),
        marker,
    ]


class RunLog:
    """The runner's own narration, redacted and kept beside the artifacts."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.commands: list[dict[str, Any]] = []

    def say(self, message: str) -> None:
        self.lines.append(f"{proc.utc_now()} {redact.text(message)}")

    def record(self, outcome: proc.CommandOutcome) -> proc.CommandOutcome:
        self.commands.append(outcome.to_record().to_document())
        self.say(
            f"ran {outcome.argv[0]} in the {outcome.context}: "
            f"exit={outcome.exit_code} timed_out={outcome.timed_out} "
            f"elapsed={outcome.elapsed_seconds}s"
        )
        return outcome

    def as_text(self) -> str:
        return "\n".join(self.lines) + "\n"

    def as_json_lines(self) -> str:
        return "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in self.commands)


@dataclass
class CycleOutcome:
    """What one call to :func:`run_cycle` produced."""

    run_result: RunResult
    artifact_dir: Path
    manifest_path: Path | None = None
    exit_code: int = 0
    blockers: list[str] = field(default_factory=list)


def _classify(
    *,
    export_status: ExportStatus,
    blocked_reason: str,
    timed_out: bool,
    error_reason: str,
    unverifiable_reason: str,
    cleanup_record: CleanupRecord,
) -> tuple[RunStatus, str]:
    if export_status is not ExportStatus.CONFIRMED:
        return (
            RunStatus.UNKNOWN,
            f"the export was not confirmed ({export_status.value}); "
            "no claim about this run rests on complete evidence",
        )
    if blocked_reason:
        return RunStatus.BLOCKED, blocked_reason
    if timed_out:
        return RunStatus.TIMEOUT, "the task reached the configured deadline"
    if error_reason:
        return RunStatus.ERROR, error_reason
    if unverifiable_reason:
        return RunStatus.UNKNOWN, unverifiable_reason
    if cleanup_record.status is not CleanupStatus.DESTROYED:
        return RunStatus.CLEANUP_FAILED, cleanup_record.reason
    return RunStatus.SETTLED, ""


class _Cycle:
    """One run, held together so the phases can share their state."""

    def __init__(
        self,
        *,
        run_config: RunConfig,
        adapter: BackendAdapter,
        run_id: str,
        host_home: Path | None,
        environ: Mapping[str, str] | None,
        tool_versions: Mapping[str, Any] | None,
        survey=None,
    ):
        self.survey = survey
        self.config = run_config
        self.adapter = adapter
        self.run_id = run_id
        self.host_home = host_home
        self.environ = environ
        self.tool_versions = dict(tool_versions or {})
        self.artifact_dir = run_config.artifact_root / run_id
        self.exporter = Exporter(self.artifact_dir)
        self.log = RunLog()
        self.result = RunResult(
            run_id=run_id,
            backend=run_config.backend,
            tested_revision=run_config.tested_revision,
            dely_revision=run_config.dely_revision,
        )
        self.handle: EnvironmentHandle | None = None
        self.app_pid: int | None = None
        self.create_attempted = False
        self.blocked_reason = ""
        self.error_reason = ""
        self.unverifiable_reason = ""
        self.timed_out = False
        self.task_ran = False
        self.stop_confirmed = True
        self.env_overlay: dict[str, str] = {}
        self.coordinator_handle: str | None = None
        self.host_before: dict[str, Any] = {}
        self.lease: admission.Lease | None = None
        self.registry_before: dict[str, Any] = {}
        self.implementer: Any = None

    # -- plumbing ---------------------------------------------------------

    @contextmanager
    def phase(self, name: str):
        record = PhaseRecord(name=name, status=PhaseStatus.OK, started_at=proc.utc_now())
        started = time.monotonic()
        self.result.phases.append(record)
        self.log.say(f"phase {name} started")
        try:
            yield record
        except Exception as error:  # a phase failure is data, not a crash
            record.status = PhaseStatus.FAILED
            record.detail = redact.text(f"{type(error).__name__}: {error}")
            self.error_reason = self.error_reason or record.detail
            self.log.say(f"phase {name} failed: {record.detail}")
        finally:
            record.finished_at = proc.utc_now()
            record.elapsed_seconds = round(time.monotonic() - started, 3)
            self.log.say(f"phase {name} ended as {record.status.value}")

    def skip(self, name: str, detail: str) -> None:
        self.result.phases.append(
            PhaseRecord(name=name, status=PhaseStatus.SKIPPED, detail=detail)
        )
        self.log.say(f"phase {name} skipped: {detail}")

    def _run_markers(self) -> tuple[str, ...]:
        """Every name that belongs to this run and to nothing else.

        These are what the operator's own Orca registry is searched for. A
        repository, a worktree, a terminal archive or an orchestration row that
        carries one of them was written by this run into somebody else's
        application.
        """
        markers = [self.run_id, str(self.config.state_root / self.run_id)]
        if self.handle is not None:
            markers.extend(
                [self.handle.environment_id, self.handle.home_path, self.handle.project_path]
            )
        planned = self.adapter.plan_handle()
        if planned is not None:
            markers.append(planned.environment_id)
        return tuple(
            dict.fromkeys(str(marker) for marker in markers if str(marker).strip())
        )

    def _snapshot_host(self) -> dict[str, Any]:
        return hostinfo.snapshot(
            state_root=self.config.state_root,
            artifact_root=self.config.artifact_root,
            extra={"backend": self.adapter.name},
        )

    def execute(self, argv, *, timeout=None, **options) -> proc.CommandOutcome:
        return self.log.record(
            self.adapter.execute(
                argv,
                timeout=self.config.timeout_seconds if timeout is None else timeout,
                extra_values=tuple(self.env_overlay.values()),
                **options,
            )
        )

    # -- phases -----------------------------------------------------------

    def run(self, staging: Path) -> CycleOutcome:
        baseline = staging / "baseline"
        fetched = staging / "fetched"
        self.result.started_at = proc.utc_now()
        started = time.monotonic()

        self._prepare(baseline)
        if not self.blocked_reason and not self.error_reason:
            self._create()
        else:
            self.skip("create", self.blocked_reason or self.error_reason)
        if self.handle is not None:
            self._bootstrap(baseline)
            self._identity()
        else:
            self.skip("bootstrap", "no environment was created")
            self.skip("identity", "no environment was created")
        if self.handle is not None and not self.blocked_reason and not self.error_reason:
            self._task()
        else:
            self.skip("task", self.blocked_reason or self.error_reason or "no environment")
        if (
            self.task_ran
            and not self.timed_out
            and not self.error_reason
            and not self.unverifiable_reason
        ):
            self._review()
        else:
            self.skip(
                "review",
                self.blocked_reason
                or self.error_reason
                or self.unverifiable_reason
                or "the task did not settle",
            )
        if (
            self.task_ran
            and not self.timed_out
            and not self.error_reason
            and not self.unverifiable_reason
        ):
            self._check()
        else:
            self.skip(
                "check",
                self.blocked_reason
                or self.error_reason
                or self.unverifiable_reason
                or "the task did not settle",
            )

        self._collect(baseline, fetched)
        export_record = self._export()
        cleanup_record = self._cleanup(export_record)
        return self._close(export_record, cleanup_record, started)

    def _admit(self) -> bool:
        """Take this host's slot for the backend, or refuse before creating anything."""
        claim = self.config.claim()
        record = AdmissionRecord(
            backend=self.config.backend,
            policy=self.config.limits.to_record(),
            claim=claim.to_document(),
        )
        try:
            self.lease = admission.acquire(
                root=self.config.state_root,
                run_id=self.run_id,
                backend=self.config.backend,
                limits=self.config.limits,
                claim=claim,
            )
        except admission.AdmissionRefused as refusal:
            record.status = PhaseStatus.BLOCKED
            record.detail = redact.text(str(refusal))
            record.occupants = [item.to_document() for item in refusal.occupants]
            self.result.admission = record
            self.blocked_reason = record.detail
            self.log.say(f"admission refused: {record.detail}")
            return False
        record.status = PhaseStatus.OK
        record.granted = True
        record.detail = (
            f"this run holds {self.lease.run_id} against a ceiling of "
            f"{self.config.limits.effective_max_active} active "
            f"{self.config.backend} environment(s)"
        )
        self.result.admission = record
        self.log.say(record.detail)
        return True

    def _prepare(self, baseline: Path) -> None:
        with self.phase("prepare"):
            # Nothing is created before this host has said it has room, so a
            # refusal costs nothing and leaves nothing to clean up.
            if not self._admit():
                self.exporter.write_json(
                    "admission.json", self.result.admission.to_document()
                )
                return
            self.exporter.write_json(
                "admission.json", self.result.admission.to_document()
            )
            self.host_before = self._snapshot_host()
            self.exporter.write_json("host-before.json", self.host_before)
            self.registry_before = hostregistry.fingerprint(
                self.host_home, markers=self._run_markers()
            )
            self.exporter.write_json(
                "host-registry-before.json", self.registry_before
            )
            commit = project.resolve_revision(
                self.config.project.source, self.config.project.revision
            )
            self.result.versions["project_revision"] = commit
            self.result.versions["configured_revision"] = self.config.project.revision
            project.export_revision(self.config.project.source, commit, baseline)
            self.log.say(f"exported the project at {commit} for the environment copy")
            report = self.adapter.preflight()
            self.exporter.write_json("preflight.json", report.to_document())
            if not report.ok:
                self.blocked_reason = "preflight refused this backend on this host: " + "; ".join(
                    f"{finding.name}: {finding.detail}" for finding in report.blockers
                )

    def _create(self) -> None:
        with self.phase("create"):
            self.create_attempted = True
            self.handle = self.adapter.create()
            self.result.environment_id = self.handle.environment_id
            self.result.environment = {
                **self.handle.to_document(),
                "backend": self.adapter.describe(),
            }
            self.exporter.write_json("backend-status.json", self.result.environment)

    def _bootstrap(self, baseline: Path) -> None:
        assert self.handle is not None
        with self.phase("bootstrap") as record:
            self.adapter.put_tree(baseline, self.handle.project_path)
            # Provisioning first: it is what gives the environment the tools the
            # rest of the bootstrap needs, git among them.
            for argv in self._provision_steps():
                record.commands.append(self.execute(list(argv)).to_record())
            # Orca registers a worktree for a repository; the exported copy is
            # not one until this makes it one.
            for argv in project.initialise_repository_commands(self.handle.project_path):
                outcome = self.execute(argv, timeout=min(300, self.config.timeout_seconds))
                record.commands.append(outcome.to_record())
                if not outcome.ok:
                    record.status = PhaseStatus.FAILED
                    record.detail = (
                        "the project copy could not be made into a repository: "
                        + redact.text((outcome.stderr or outcome.stdout).strip()[-300:])
                    )
                    self.error_reason = self.error_reason or record.detail
                    return
            auth_record, overlay = auth.bootstrap(
                run_config=self.config,
                adapter=self.adapter,
                handle=self.handle,
                host_home=self.host_home,
                environ=self.environ,
            )
            self.result.auth = auth_record
            self.env_overlay = dict(overlay)
            self.exporter.write_json("auth-receipt.json", auth_record.to_document())
            if auth_record.status is PhaseStatus.BLOCKED:
                record.status = PhaseStatus.BLOCKED
                self.blocked_reason = auth_record.detail
                return
            # Auth declares what it wants in the per-run settings; this writes
            # that file, so it runs after auth rather than before it.
            first_run = firstrun.apply(
                run_config=self.config, adapter=self.adapter, handle=self.handle
            )
            self.result.first_run = first_run
            self.exporter.write_json("first-run-state.json", first_run.to_document())
            # Provisioning claims to have installed the skills; this asks the
            # environment what it actually has, and at which revision.
            self._verify_skills(record)

    def _verify_skills(self, record) -> None:
        """Ask the environment which skills it has, and whether they are the pinned ones."""
        assert self.handle is not None
        settings = self.config.skills
        if settings.empty:
            self.result.skills = skills.record([], settings.required)
            self.exporter.write_json(
                "skills.json", self.result.skills.to_document(), required=False
            )
            return
        skill_answers: dict[str, tuple[str, str]] = {}
        plugin_answers: dict[str, tuple[str, str]] = {}
        if settings.bundled:
            outcome = self.execute(
                skills.locate_argv(
                    settings.roots, [item.name for item in settings.bundled]
                ),
                timeout=min(180, self.config.timeout_seconds),
            )
            record.commands.append(outcome.to_record())
            skill_answers = skills.parse(outcome.stdout)
        installed_counts: dict[str, tuple[int, int]] = {}
        if settings.plugins:
            pairs = [(item.name, item.path) for item in settings.plugins]
            outcome = self.execute(
                skills.revision_argv(pairs),
                timeout=min(180, self.config.timeout_seconds),
            )
            record.commands.append(outcome.to_record())
            plugin_answers = skills.parse(outcome.stdout)
            # Being at the pinned commit is not the same as the agent being able
            # to read what is in it.
            outcome = self.execute(
                skills.installed_argv(settings.roots, pairs),
                timeout=min(300, self.config.timeout_seconds),
            )
            record.commands.append(outcome.to_record())
            installed_counts = skills.counts(outcome.stdout)
        findings, _ = skills.judge(
            bundled=[(item.name, item.sha256) for item in settings.bundled],
            plugins=[(item.name, item.revision) for item in settings.plugins],
            skill_answers=skill_answers,
            plugin_answers=plugin_answers,
            installed_counts=installed_counts,
        )
        self.result.skills = skills.record(findings, settings.required)
        self.exporter.write_json("skills.json", self.result.skills.to_document())
        self.log.say(f"skills: {self.result.skills.detail}")
        if self.result.skills.status is PhaseStatus.BLOCKED:
            record.status = PhaseStatus.BLOCKED
            record.detail = self.result.skills.detail
            self.blocked_reason = self.blocked_reason or self.result.skills.detail

    def _provision_steps(self):
        section = (
            self.config.distrobox if self.config.backend == "distrobox" else self.config.vm
        )
        return getattr(section, "provision", ()) if section is not None else ()

    def _identity(self) -> None:
        assert self.handle is not None
        with self.phase("identity") as record:
            host_probe = self.log.record(
                proc.run(
                    probe.probe_argv(
                        str(self.config.project.source), self.config.orca.command
                    ),
                    timeout=min(120, self.config.timeout_seconds),
                    context="host",
                )
            )
            environment_probe = self.execute(
                probe.probe_argv(self.handle.project_path, self.config.orca.command),
                timeout=min(120, self.config.timeout_seconds),
            )
            self.exporter.write_text("identity/host-probe.txt", host_probe.stdout)
            self.exporter.write_text(
                "identity/environment-probe.txt", environment_probe.stdout
            )
            record.commands.extend(
                [host_probe.to_record(), environment_probe.to_record()]
            )
            identity = probe.verdict(
                host=probe.parse(host_probe.stdout),
                environment=probe.parse(environment_probe.stdout),
                backend=self.config.backend,
                expected_home=self.handle.home_path,
                expected_project=self.handle.project_path,
            )
            orca_status = self.execute(
                list(self.config.orca.status_argv), timeout=min(120, self.config.timeout_seconds)
            )
            identity.orca_status_exit_code = orca_status.exit_code
            self.exporter.write_text(
                "identity/orca-status.json", orca_status.stdout or "{}\n"
            )
            record.commands.append(orca_status.to_record())
            self.result.identity = identity
            allowed, reason = probe.may_continue(identity)
            if not allowed:
                record.status = PhaseStatus.BLOCKED
                record.detail = reason
                self.blocked_reason = self.blocked_reason or reason
                return
            # A command that resolves is not a runtime that can take a dispatch.
            # Nothing else starts the application, so the runner does — but only
            # when it is not already there, because a second one takes the
            # singleton lock from the first and neither finishes starting.
            runtime = orca.read_status(
                self.adapter, self.config.orca.status_argv, timeout=120
            )
            windows_before = self._windows()
            if not runtime.ready:
                started = self.execute(
                    orca.start_argv(self.config.orca.app_argv, self.config.orca.display),
                    timeout=min(180, self.config.timeout_seconds),
                )
                record.commands.append(started.to_record())
                # Only meaningful where the environment's process table is the
                # host's; elsewhere the same number is somebody else's process.
                if self.adapter.shares_host_processes:
                    self.app_pid = orca.started_pid(started)
            runtime = orca.wait_for_runtime(
                self.adapter,
                self.config.orca.status_argv,
                timeout=self.config.orca.ready_timeout_seconds,
            )
            identity.orca_runtime = runtime.to_document()
            self.exporter.write_json("identity/orca-runtime.json", runtime.to_document())
            if not runtime.ready:
                record.status = PhaseStatus.BLOCKED
                record.detail = (
                    "orca is installed in the environment but its runtime never became "
                    f"ready there: {runtime.detail}"
                )
                self.blocked_reason = self.blocked_reason or record.detail
                return
            if not self._check_display(record, windows_before):
                return
            try:
                self.coordinator_handle = orca.open_coordinator_terminal(
                    self.adapter,
                    self.handle.project_path,
                    timeout=min(300, self.config.timeout_seconds),
                    expected_host=self.handle.environment_id,
                )
            except orca.OrcaSessionError as error:
                record.status = PhaseStatus.BLOCKED
                record.detail = str(error)
                self.blocked_reason = self.blocked_reason or record.detail
                return
            record.detail = (
                f"orca runtime {runtime.runtime_id} is ready and the coordinator "
                f"terminal is {self.coordinator_handle}"
            )

    def _task(self) -> None:
        assert self.handle is not None
        with self.phase("task") as record:
            self.task_ran = True
            worker_record = worker.launch(
                run_config=self.config,
                adapter=self.adapter,
                handle=self.handle,
                timeout_seconds=self.config.timeout_seconds,
                env_overlay=self.env_overlay,
                coordinator_handle=self.coordinator_handle,
                keep=self._keep_reply,
            )
            self.result.worker = worker_record
            self.implementer = worker_record
            record.status = worker_record.status
            record.detail = worker_record.detail
            if worker_record.status is PhaseStatus.TIMEOUT:
                self.timed_out = True
            elif worker_record.status is not PhaseStatus.OK:
                if worker_record.outcome in worker.UNVERIFIABLE_STATES:
                    self.unverifiable_reason = self.unverifiable_reason or (
                        f"the execution plane reported the dispatch as "
                        f"{worker_record.outcome}, which is unverifiable rather than "
                        "failed: nothing here knows whether the worker ran"
                    )
                else:
                    self.error_reason = self.error_reason or worker_record.detail

    def _gui_mode(self) -> str:
        """Which screen this environment's application is pointed at.

        Only the container backend can reach the operator's, so only it has a
        choice to declare. A guest has its own screen by construction.
        """
        if self.config.backend == "distrobox" and self.config.distrobox is not None:
            return self.config.distrobox.gui
        return display_module.VIRTUAL

    def _windows(self) -> tuple[bool, list]:
        """Ask the environment's own screen what is on it."""
        outcome = self.execute(
            display_module.windows_argv(self.config.orca.display),
            timeout=min(120, self.config.timeout_seconds),
        )
        return display_module.parse(outcome.stdout)

    def _check_display(self, record, windows_before) -> bool:
        """Establish that the application went to this run's screen, not somebody's."""
        reachable_before, before = windows_before
        reachable, after = self._windows()
        mode = self._gui_mode()
        leaking = display_module.carrying_host_session(
            processes.holding([str(self.config.state_root / self.run_id)])
            if self.adapter.shares_host_processes
            else []
        )
        ok, detail = display_module.verdict(
            mode=mode,
            reachable=reachable or reachable_before,
            before=before,
            after=after,
            application="orca",
            leaking=leaking,
        )
        result = DisplayRecord(
            status=PhaseStatus.OK if ok else PhaseStatus.BLOCKED,
            mode=mode,
            display=self.config.orca.display,
            reachable=reachable,
            before=[window.to_document() for window in before],
            after=[window.to_document() for window in after],
            appeared=[
                window.to_document()
                for window in display_module.appeared(before, after)
            ],
            detail=detail,
        )
        self.result.display = result
        self.exporter.write_json("display.json", result.to_document())
        self.log.say(f"display: {detail}")
        if ok:
            return True
        record.status = PhaseStatus.BLOCKED
        record.detail = detail
        self.blocked_reason = self.blocked_reason or (
            "this run cannot show that its application stayed on its own screen: "
            + detail
        )
        return False

    def _review(self) -> None:
        """Hand the implementer's diff to a reviewer that did not write it."""
        assert self.handle is not None
        if not self.config.review.enabled:
            self.skip("review", "this configuration asks for no review")
            return
        with self.phase("review") as record:
            outcome = self._handoff(record)
            if outcome is not None:
                record.status = outcome
                if outcome is PhaseStatus.FAILED:
                    self.error_reason = self.error_reason or self.result.review.detail

    def _handoff(self, record) -> PhaseStatus | None:
        assert self.handle is not None
        home = Path(self.handle.home_path)
        diff_path = str(home / review_module.DIFF_NAME)
        verdict_path = str(home / review_module.VERDICT_NAME)
        result = self.result.review
        result.diff_path = diff_path

        captured = self.execute(
            review_module.capture_argv(self.handle.project_path, diff_path),
            timeout=min(300, self.config.timeout_seconds),
        )
        record.commands.append(captured.to_record())
        if not captured.ok:
            result.status = PhaseStatus.FAILED
            result.detail = (
                "the implementer's diff could not be captured, so there is "
                "nothing to hand over: "
                + redact.text((captured.stderr or captured.stdout).strip()[-300:])
            )
            self.exporter.write_json("review.json", result.to_document())
            return PhaseStatus.FAILED
        measured = review_module.parse_capture(captured.stdout)
        result.diff_sha256 = str(measured.get("sha256") or "")
        result.diff_lines = int(measured.get("lines") or 0)
        # The diff the reviewer is given is an artifact of this run too: a
        # verdict nobody can read the subject of is not evidence.
        fetched = self.execute(
            review_module.read_argv(diff_path),
            timeout=min(120, self.config.timeout_seconds),
        )
        record.commands.append(fetched.to_record())
        self.exporter.write_text(
            "handoff-diff.patch", fetched.stdout, required=False
        )

        reviewer = worker.launch(
            run_config=self.config,
            adapter=self.adapter,
            handle=self.handle,
            timeout_seconds=self.config.timeout_seconds,
            env_overlay=self.env_overlay,
            coordinator_handle=self.coordinator_handle,
            keep=self._keep_reply,
            role="reviewer",
            prompt_name=review_module.PROMPT_NAME,
            prompt_text=review_module.build_prompt(
                diff_path=diff_path,
                project_path=self.handle.project_path,
                verdict_path=verdict_path,
                relative_path=self.config.task.relative_path,
                marker=self.config.task.marker,
            ),
            orca_run_id=self.implementer.run_id if self.implementer else None,
        )
        self.result.reviewer = reviewer
        result.reviewer = reviewer.to_document()
        record.commands.extend(reviewer.commands)

        after = self.execute(
            review_module.digest_argv(diff_path),
            timeout=min(120, self.config.timeout_seconds),
        )
        record.commands.append(after.to_record())
        result.diff_sha256_after = str(
            review_module.parse_capture(after.stdout).get("sha256") or ""
        )

        verdict_read = self.execute(
            review_module.read_argv(verdict_path),
            timeout=min(120, self.config.timeout_seconds),
        )
        record.commands.append(verdict_read.to_record())
        verdict = review_module.parse_verdict(verdict_read.stdout)
        result.verdict = str(verdict.get("verdict") or "")
        result.reason = redact.text(str(verdict.get("reason") or ""))
        result.diff_reported_by_reviewer = str(verdict.get("diff_sha256") or "")

        separation = review_module.separation(self.implementer, reviewer)
        result.separation = separation.to_document()
        same, why = review_module.judge_same_diff(
            captured=result.diff_sha256,
            after=result.diff_sha256_after,
            reported=result.diff_reported_by_reviewer,
        )
        result.same_diff = same
        result.same_diff_detail = why

        if reviewer.status is PhaseStatus.TIMEOUT:
            result.status = PhaseStatus.TIMEOUT
            result.detail = "the reviewer reached the run deadline"
            self.timed_out = True
        elif reviewer.status is not PhaseStatus.OK:
            result.status = PhaseStatus.FAILED
            result.detail = f"the reviewer did not settle: {reviewer.detail}"
        elif not separation.ok:
            result.status = PhaseStatus.FAILED
            result.detail = (
                "the review was not independent of the work it reviewed: "
                + separation.detail
            )
        elif not same:
            result.status = PhaseStatus.FAILED
            result.detail = why
        else:
            result.status = PhaseStatus.OK
            result.detail = (
                f"the reviewer returned {result.verdict!r} on the implementer's "
                f"diff ({result.diff_lines} line(s)); {separation.detail}"
            )
        self.exporter.write_json("review.json", result.to_document())
        self.log.say(f"review: {result.detail}")
        return result.status

    def _keep_reply(self, name: str, stdout: str, stderr: str) -> None:
        self.exporter.write_text(f"dispatch/{name}.stdout", stdout)
        if stderr.strip():
            self.exporter.write_text(f"dispatch/{name}.stderr", stderr)

    def _check(self) -> None:
        assert self.handle is not None
        with self.phase("check") as record:
            argv = check_argv(
                self.handle.project_path,
                self.config.check.relative_path,
                self.config.check.expected_marker,
            )
            outcome = self.execute(argv, timeout=min(300, self.config.timeout_seconds))
            self.exporter.write_text("check.stdout", outcome.stdout)
            self.exporter.write_text("check.stderr", outcome.stderr)
            record.commands.append(
                outcome.to_record(stdout_path="check.stdout", stderr_path="check.stderr")
            )
            self.result.check = CheckRecord(
                status=PhaseStatus.OK if outcome.ok else PhaseStatus.FAILED,
                argv=outcome.argv,
                exit_code=outcome.exit_code,
                started_at=outcome.started_at,
                finished_at=outcome.finished_at,
                elapsed_seconds=outcome.elapsed_seconds,
                stdout_path="check.stdout",
                stderr_path="check.stderr",
                expected_marker=self.config.check.expected_marker,
                detail=(
                    "the independent check observed the marker"
                    if outcome.ok
                    else f"the independent check exited {outcome.exit_code}"
                ),
            )
            record.status = self.result.check.status
            if not outcome.ok:
                self.error_reason = self.error_reason or self.result.check.detail

    def _collect(self, baseline: Path, fetched: Path) -> None:
        if self.handle is None:
            self.skip("collect", "no environment was created")
            return
        with self.phase("collect") as record:
            try:
                self.adapter.fetch_tree(self.handle.project_path, fetched)
            except Exception as error:
                record.status = PhaseStatus.FAILED
                record.detail = redact.text(
                    f"the project tree could not be brought out: {error}"
                )
                self.exporter.declare("diff.patch", required=self.task_ran)
                self.log.say(record.detail)
                return
            patch = project.unified_diff(baseline, fetched)
            self.exporter.write_text("diff.patch", patch, required=self.task_ran)
            artifact = fetched / self.config.task.relative_path
            if artifact.is_file():
                self.exporter.write_bytes(
                    f"task-artifact/{self.config.task.relative_path}",
                    artifact.read_bytes(),
                    required=False,
                )
            else:
                self.exporter.declare(
                    f"task-artifact/{self.config.task.relative_path}", required=False
                )
            record.detail = f"{len(patch.splitlines())} patch line(s) collected"

    def _export(self):
        with self.phase("export") as record:
            self.exporter.write_json(
                "run-before-cleanup.json", self.result.to_document()
            )
            self.exporter.write_text("logs/runner.log", self.log.as_text())
            self.exporter.write_text("logs/commands.jsonl", self.log.as_json_lines())
            export_record = self.exporter.confirm()
            self.result.export = export_record
            record.status = (
                PhaseStatus.OK
                if export_record.status is ExportStatus.CONFIRMED
                else PhaseStatus.FAILED
            )
            record.detail = export_record.detail
            return export_record

    def _cleanup(self, export_record) -> CleanupRecord:
        if self.handle is None:
            record = self._cleanup_without_a_handle()
            self.result.cleanup = record
            self.skip("cleanup", record.reason)
            return record
        with self.phase("cleanup") as phase_record:
            self.result.auth = auth.teardown(
                adapter=self.adapter, handle=self.handle, record=self.result.auth
            )
            stop_report = self.adapter.stop()
            self.stop_confirmed = stop_report.confirmed
            self.log.say(f"stop confirmed={stop_report.confirmed}: {stop_report.detail}")
            record = cleanup.perform(
                adapter=self.adapter,
                handle=self.handle,
                export_record=export_record,
                stop_confirmed=self.stop_confirmed,
                survey=self.survey,
                started=[self.app_pid] if self.app_pid else [],
            )
            record = self._check_host_registry(record)
            self.result.cleanup = record
            phase_record.status = (
                PhaseStatus.OK
                if record.status is CleanupStatus.DESTROYED
                else PhaseStatus.FAILED
            )
            phase_record.detail = record.reason
            return record

    def _check_host_registry(self, record: CleanupRecord) -> CleanupRecord:
        """Ask whether the environment wrote itself into the operator's own Orca.

        The environment has its own profile and its own home, but nothing makes
        that true: a container inherits the host's session, and an application
        that reaches the host's socket registers into the host's registry. An
        entry naming this run is this run's residue, sitting somewhere cleanup
        must not reach — so it is reported, never removed.
        """
        after = hostregistry.fingerprint(self.host_home, markers=self._run_markers())
        clean, reason = hostregistry.verdict(after)
        document = {
            "clean": clean,
            "reason": reason,
            "before": self.registry_before,
            "after": after,
            "difference": hostregistry.difference(self.registry_before, after),
        }
        record.host_registry = document
        self.exporter.write_json("host-registry-after.json", after, required=False)
        self.log.say(f"host registry: {reason}")
        if clean:
            return record
        return replace(
            record,
            status=CleanupStatus.RESIDUE,
            reason=(
                f"{record.reason}; {reason}. Nothing here removes an entry from "
                "the operator's own registry, so it needs their decision"
            ).lstrip("; "),
            verified=False,
            host_registry=document,
        )

    def _cleanup_without_a_handle(self) -> CleanupRecord:
        """Report what a run that never got a handle may still have left."""
        planned = self.create_attempted and self.adapter.plan_handle()
        if not planned:
            return CleanupRecord(
                status=CleanupStatus.DESTROYED,
                reason="no environment was created, so no per-run resource existed",
                verified=True,
            )
        return CleanupRecord(
            status=CleanupStatus.RESIDUE,
            reason=(
                "create did not return a handle, so the state of these per-run "
                "resources is unknown; nothing was destroyed blind and they need a "
                "manual decision"
            ),
            retained=[str(item) for item in planned.per_run_resources],
            shared_preserved=[str(item) for item in planned.shared_resources],
            verified=False,
        )

    def _surrender(self, cleanup_record: CleanupRecord) -> None:
        """Give the slot back, or keep it so the next run meets this one's residue."""
        if self.lease is None:
            return
        confirmed = (
            cleanup_record.status is CleanupStatus.DESTROYED and self.stop_confirmed
        )
        reason = (
            "cleanup destroyed every per-run resource and the stop was confirmed"
            if confirmed
            else (
                f"cleanup ended as {cleanup_record.status.value} "
                f"(stop_confirmed={self.stop_confirmed}): {cleanup_record.reason}"
            )
        )
        admission.release(self.lease, confirmed=confirmed, reason=redact.text(reason))
        self.result.admission.released = confirmed
        self.result.admission.detail = (
            self.result.admission.detail
            + ("; the slot was released" if confirmed else f"; the slot is kept because {reason}")
        )
        self.exporter.write_json(
            "admission.json", self.result.admission.to_document(), required=False
        )
        self.log.say(
            "admission slot released" if confirmed else f"admission slot kept: {reason}"
        )

    def _close(self, export_record, cleanup_record, started: float) -> CycleOutcome:
        with self.phase("close"):
            self.result.finished_at = proc.utc_now()
            self.result.elapsed_seconds = round(time.monotonic() - started, 3)
            run_status, classification = _classify(
                export_status=export_record.status,
                blocked_reason=self.blocked_reason,
                timed_out=self.timed_out,
                error_reason=self.error_reason,
                unverifiable_reason=self.unverifiable_reason,
                cleanup_record=cleanup_record,
            )
            self.result.status = run_status
            self.result.failure_classification = classification
            self._surrender(cleanup_record)
            host_after = self._snapshot_host()
            self.exporter.write_json("host-after.json", host_after, required=False)
            self.exporter.write_json(
                "cleanup.json",
                {
                    **cleanup_record.to_document(),
                    "stop_confirmed": self.stop_confirmed,
                    "host_difference": hostinfo.difference(self.host_before, host_after),
                },
                required=False,
            )
            document = manifest.build(
                run_result=self.result,
                run_config=self.config,
                host_before=self.host_before,
                host_after=host_after,
                artifacts=export_record.artifacts,
                tool_versions=self.tool_versions,
                generated_at=proc.utc_now(),
            )
            manifest_path = self.artifact_dir / "manifest.json"
            manifest.write(document, manifest_path)
        return CycleOutcome(
            run_result=self.result,
            artifact_dir=self.artifact_dir,
            manifest_path=manifest_path,
            exit_code=exit_code(self.result.status),
            blockers=[self.blocked_reason] if self.blocked_reason else [],
        )


def run_cycle(
    *,
    run_config: RunConfig,
    adapter: BackendAdapter,
    run_id: str,
    host_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    tool_versions: Mapping[str, Any] | None = None,
    survey=None,
) -> CycleOutcome:
    """Run one minimal proof cycle and return its result and artifacts.

    `survey` is how cleanup finds processes this run left on the host. It is
    passed in rather than defaulted, so a test that does not ask for it can
    never reach the machine running the tests.
    """
    cycle = _Cycle(
        run_config=run_config,
        adapter=adapter,
        run_id=run_id,
        host_home=host_home,
        environ=environ,
        tool_versions=tool_versions,
        survey=survey,
    )
    with tempfile.TemporaryDirectory(prefix="dely-cycle-") as staging:
        return cycle.run(Path(staging))
