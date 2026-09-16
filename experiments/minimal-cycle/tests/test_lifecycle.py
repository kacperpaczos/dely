"""The shared lifecycle: export before destroy, and never a silent host fallback."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cycle_runner import admission, config as config_module, ids, lifecycle, status
from tests.fakes import FakeAdapter
from tests.test_config import minimal_document

RUN_ID = "20260914T221530Z-abc123-0123abcd"
MARKER = "dely-cycle-marker"


def make_source_repo(root: Path) -> Path:
    repo = root / "under-test"
    repo.mkdir(parents=True)
    for arguments in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "cycle@example.invalid"),
        ("config", "user.name", "cycle"),
    ):
        subprocess.run(["git", "-C", str(repo), *arguments], check=True, capture_output=True)
    (repo / "readme.md").write_text("project under test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    return repo


class CycleTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_source_repo(self.root / "src")
        self.artifacts = self.root / "artifacts"
        self.state = self.root / "state"
        self.host_home = self.root / "hosthome"
        (self.host_home / ".claude").mkdir(parents=True)
        (self.host_home / ".claude" / ".credentials.json").write_text(
            '{"note": "stand-in"}', encoding="utf-8"
        )
        self.addCleanup(self._tmp.cleanup)

    def make_config(self, **overrides):
        document = minimal_document()
        document["artifact_root"] = str(self.artifacts)
        document["state_root"] = str(self.state)
        document["project"]["source"] = str(self.repo)
        document["project"]["revision"] = "main"
        document["task"]["marker"] = MARKER
        # These tests drive a fake environment, so a runtime that is never going
        # to be ready should be given up on at once rather than in five minutes.
        document["orca"]["ready_timeout_seconds"] = 1
        document.update(overrides)
        return config_module.from_document(document)

    def run_cycle(self, config_document=None, **adapter_options):
        run_config = (
            config_module.from_document(config_document)
            if config_document is not None
            else self.make_config()
        )
        adapter = FakeAdapter(
            self.state / RUN_ID, host_project=self.repo, **adapter_options
        )
        outcome = lifecycle.run_cycle(
            run_config=run_config,
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )
        return adapter, outcome

    def artifact(self, relative):
        return (self.artifacts / RUN_ID / relative)


class SettledCycleTest(CycleTestCase):
    def test_a_full_cycle_settles(self):
        _, outcome = self.run_cycle()
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED, outcome.run_result.failure_classification)
        self.assertEqual(outcome.exit_code, 0)

    def test_the_phases_run_in_the_documented_order(self):
        adapter, outcome = self.run_cycle()
        names = [phase.name for phase in outcome.run_result.phases]
        self.assertEqual(
            names,
            [
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
            ],
        )

    def test_the_export_happens_before_the_environment_is_destroyed(self):
        adapter, outcome = self.run_cycle()
        self.assertIn("fetch_tree", adapter.calls)
        self.assertIn("destroy", adapter.calls)
        self.assertLess(adapter.calls.index("fetch_tree"), adapter.calls.index("destroy"))
        self.assertLess(adapter.calls.index("stop"), adapter.calls.index("destroy"))

    def test_every_required_artifact_is_on_the_host(self):
        _, outcome = self.run_cycle()
        for relative in (
            "manifest.json",
            "host-before.json",
            "host-after.json",
            "backend-status.json",
            "auth-receipt.json",
            "preflight.json",
            "identity/host-probe.txt",
            "identity/environment-probe.txt",
            "identity/orca-status.json",
            "check.stdout",
            "check.stderr",
            "diff.patch",
            "logs/runner.log",
            "logs/commands.jsonl",
            "run-before-cleanup.json",
            "export-receipt.json",
            "cleanup.json",
        ):
            self.assertTrue(self.artifact(relative).is_file(), f"missing {relative}")

    def test_the_task_artifact_is_exported(self):
        _, outcome = self.run_cycle()
        self.assertEqual(
            self.artifact("task-artifact/evidence.txt").read_text(encoding="utf-8"),
            MARKER,
        )

    def test_the_diff_shows_what_the_task_changed(self):
        _, outcome = self.run_cycle()
        patch = self.artifact("diff.patch").read_text(encoding="utf-8")
        self.assertIn("+++ b/evidence.txt", patch)
        self.assertIn(f"+{MARKER}", patch)

    def test_the_manifest_validates_and_names_the_run(self):
        _, outcome = self.run_cycle()
        document = json.loads(self.artifact("manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(document["run_id"], RUN_ID)
        self.assertEqual(document["status"], "SETTLED")
        self.assertEqual(document["cleanup"]["status"], "DESTROYED")
        self.assertEqual(document["export"]["status"], "CONFIRMED")
        self.assertTrue(document["artifacts"])

    def test_the_environment_is_gone_and_the_shared_base_is_not(self):
        adapter, _ = self.run_cycle()
        self.assertFalse(adapter.home.exists())
        self.assertTrue(adapter.shared_base.is_file())

    def test_the_check_really_ran_against_the_environment_copy(self):
        _, outcome = self.run_cycle()
        check = outcome.run_result.check
        self.assertEqual(check.exit_code, 0)
        self.assertEqual(check.status, status.PhaseStatus.OK)
        self.assertIn("marker matched", self.artifact("check.stdout").read_text(encoding="utf-8"))


class HostFallbackTest(CycleTestCase):
    def test_a_host_identity_blocks_before_the_worker_runs(self):
        adapter, outcome = self.run_cycle(identity="host")
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertNotIn("worker", adapter.calls)
        self.assertEqual(
            outcome.run_result.identity.verdict, "HOST_FALLBACK"
        )

    def test_a_blocked_run_still_exports_and_cleans_up(self):
        adapter, outcome = self.run_cycle(identity="host")
        self.assertEqual(outcome.run_result.export.status, status.ExportStatus.CONFIRMED)
        self.assertEqual(outcome.run_result.cleanup.status, status.CleanupStatus.DESTROYED)
        self.assertTrue(self.artifact("manifest.json").is_file())

    def test_a_missing_orca_blocks_rather_than_using_the_host(self):
        adapter, outcome = self.run_cycle(orca_present=False)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("orca", outcome.run_result.failure_classification.lower())
        self.assertNotIn("worker", adapter.calls)

    def test_a_blocked_run_exits_with_its_own_code(self):
        _, outcome = self.run_cycle(identity="host")
        self.assertEqual(outcome.exit_code, status.exit_code(status.RunStatus.BLOCKED))


class ExportGateTest(CycleTestCase):
    def test_no_destroy_when_export_unconfirmed(self):
        adapter, outcome = self.run_cycle(fetch_fails=True)
        self.assertNotIn("destroy", adapter.calls)
        self.assertEqual(outcome.run_result.cleanup.status, status.CleanupStatus.RESIDUE)
        self.assertNotEqual(outcome.run_result.export.status, status.ExportStatus.CONFIRMED)
        self.assertEqual(outcome.run_result.status, status.RunStatus.UNKNOWN)
        self.assertTrue(adapter.home.exists())

    def test_an_unconfirmed_stop_prevents_destroy(self):
        adapter, outcome = self.run_cycle(stop_confirmed=False)
        self.assertNotIn("destroy", adapter.calls)
        self.assertEqual(outcome.run_result.cleanup.status, status.CleanupStatus.RESIDUE)
        self.assertIn("stop", outcome.run_result.cleanup.reason.lower())

    def test_surviving_resources_are_reported_as_cleanup_failed(self):
        _, outcome = self.run_cycle(leave_residue=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.CLEANUP_FAILED)
        self.assertEqual(outcome.run_result.cleanup.status, status.CleanupStatus.RESIDUE)


class TimeoutTest(CycleTestCase):
    def test_timeout_exports_before_stop_and_settles_timeout(self):
        adapter, outcome = self.run_cycle(task_hangs=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.TIMEOUT)
        self.assertLess(adapter.calls.index("fetch_tree"), adapter.calls.index("stop"))
        self.assertEqual(outcome.run_result.export.status, status.ExportStatus.CONFIRMED)
        self.assertTrue(self.artifact("diff.patch").is_file())
        self.assertTrue(self.artifact("manifest.json").is_file())

    def test_a_timed_out_run_never_reports_a_check_result(self):
        _, outcome = self.run_cycle(task_hangs=True)
        self.assertEqual(outcome.run_result.check.status, status.PhaseStatus.SKIPPED)


class CheckFailureTest(CycleTestCase):
    def test_a_missing_marker_is_an_error_not_a_settled_run(self):
        _, outcome = self.run_cycle(task_writes_nothing=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.ERROR)
        self.assertEqual(outcome.run_result.check.status, status.PhaseStatus.FAILED)
        self.assertNotEqual(outcome.run_result.check.exit_code, 0)

    def test_a_wrong_marker_is_an_error(self):
        _, outcome = self.run_cycle(marker="not-the-marker")
        self.assertEqual(outcome.run_result.status, status.RunStatus.ERROR)
        self.assertIn(
            "mismatch", self.artifact("check.stderr").read_text(encoding="utf-8")
        )


class PreflightTest(CycleTestCase):
    def test_a_failed_preflight_blocks_without_creating_anything(self):
        adapter, outcome = self.run_cycle(preflight_ok=False)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertNotIn("create", adapter.calls)
        self.assertTrue(self.artifact("preflight.json").is_file())
        self.assertTrue(self.artifact("manifest.json").is_file())


class RunIdentifierTest(CycleTestCase):
    def test_the_artifact_directory_is_named_by_the_run(self):
        _, outcome = self.run_cycle()
        self.assertEqual(outcome.artifact_dir, self.artifacts / RUN_ID)
        self.assertTrue(ids.is_run_id(outcome.run_result.run_id))


class HostOrcaTest(CycleTestCase):
    """Reproduces a real Distrobox run: the box saw the host's own orca.

    Distrobox mounts the host home and preserves PATH, so `command -v orca`
    resolved to the host launcher. The environment verdict was correct and the
    run still had no Orca of its own.
    """

    @unittest.skipUnless(shutil.which("orca"), "no orca on this host to resolve to")
    def test_an_orca_that_is_the_hosts_own_blocks_before_the_worker(self):
        adapter, outcome = self.run_cycle(orca_path_from_host=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertNotIn("worker", adapter.calls)
        self.assertTrue(outcome.run_result.identity.orca_is_host_installation)
        self.assertIn("host", outcome.run_result.failure_classification.lower())

    @unittest.skipUnless(shutil.which("orca"), "no orca on this host to resolve to")
    def test_that_run_still_exports_and_cleans_up(self):
        adapter, outcome = self.run_cycle(orca_path_from_host=True)
        self.assertEqual(outcome.run_result.export.status, status.ExportStatus.CONFIRMED)
        self.assertEqual(outcome.run_result.cleanup.status, status.CleanupStatus.DESTROYED)


class CreateFailureTest(CycleTestCase):
    """A create that fails partway may already have made resources."""

    def test_a_failed_create_records_the_planned_resources_as_residue(self):
        adapter, outcome = self.run_cycle(create_fails=True)
        record = outcome.run_result.cleanup
        self.assertEqual(record.status, status.CleanupStatus.RESIDUE)
        self.assertTrue(
            any(str(adapter.home) in item for item in record.retained),
            record.retained,
        )
        self.assertIn("create", record.reason.lower())

    def test_a_failed_create_does_not_destroy_blind(self):
        adapter, _ = self.run_cycle(create_fails=True)
        self.assertNotIn("destroy", adapter.calls)

    def test_a_failed_create_still_writes_a_manifest(self):
        _, outcome = self.run_cycle(create_fails=True)
        self.assertTrue(self.artifact("manifest.json").is_file())
        self.assertEqual(outcome.run_result.status, status.RunStatus.ERROR)


class OrcaSessionTest(CycleTestCase):
    """Orca being installed is not Orca being able to take a dispatch."""

    def test_a_runtime_that_never_becomes_ready_blocks_before_the_worker(self):
        adapter, outcome = self.run_cycle(runtime_ready=False)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertNotIn("worker", adapter.calls)
        self.assertIn("runtime", outcome.run_result.failure_classification.lower())

    def test_a_refused_coordinator_terminal_blocks_before_the_worker(self):
        adapter, outcome = self.run_cycle(terminal_refused=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertNotIn("worker", adapter.calls)
        self.assertIn("terminal", outcome.run_result.failure_classification.lower())

    def test_a_ready_runtime_is_recorded_with_its_identifier(self):
        _, outcome = self.run_cycle()
        runtime = outcome.run_result.identity.orca_runtime
        self.assertTrue(runtime["ready"])
        self.assertEqual(runtime["runtime_id"], "runtime-fake")
        self.assertEqual(runtime["desktop_window"], "available")
        self.assertIn("orchestration.contract.v1", runtime["capabilities"])

    def test_the_runtime_report_is_exported(self):
        self.run_cycle()
        self.assertTrue(self.artifact("identity/orca-runtime.json").is_file())

    def test_the_coordinator_terminal_is_opened_before_the_dispatch(self):
        adapter, _ = self.run_cycle()
        self.assertIn("terminal-create", adapter.calls)
        self.assertLess(adapter.calls.index("terminal-create"), adapter.calls.index("worker"))


class OrcaApplicationStartTest(CycleTestCase):
    """Nothing else starts the application, so the runner does."""

    def test_the_application_is_started_inside_the_environment(self):
        adapter, _ = self.run_cycle()
        self.assertIn("orca-start", adapter.calls)
        self.assertLess(adapter.calls.index("orca-start"), adapter.calls.index("worker"))

    def test_an_already_ready_runtime_is_not_started_again(self):
        adapter, outcome = self.run_cycle(already_running=True)
        self.assertNotIn("orca-start", adapter.calls)
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)

    def test_a_runtime_that_stays_down_after_a_start_blocks(self):
        adapter, outcome = self.run_cycle(runtime_ready=False)
        self.assertIn("orca-start", adapter.calls)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)

    def test_the_start_is_recorded_as_a_command_of_the_identity_phase(self):
        _, outcome = self.run_cycle()
        identity = outcome.run_result.phase("identity")
        self.assertTrue(
            any("orca-start" in " ".join(c.argv) for c in identity.commands),
            [" ".join(c.argv)[:60] for c in identity.commands],
        )


class ProjectRepositoryTest(CycleTestCase):
    """Orca registers a worktree for a repository, so the copy becomes one."""

    def test_the_copy_is_initialised_as_a_repository_and_it_succeeds(self):
        _, outcome = self.run_cycle()
        bootstrap = outcome.run_result.phase("bootstrap")
        commands = [c for c in bootstrap.commands if c.argv and c.argv[0] == "git"]
        self.assertGreaterEqual(len(commands), 5, [c.argv for c in bootstrap.commands])
        for command in commands:
            self.assertEqual(command.exit_code, 0, " ".join(command.argv))
        joined = " ".join(" ".join(c.argv) for c in commands)
        self.assertIn("init", joined)
        self.assertIn("commit", joined)

    def test_a_failed_initialisation_stops_the_run_rather_than_continuing(self):
        adapter, outcome = self.run_cycle(refuse_repository=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.ERROR)
        self.assertIn("repository", outcome.run_result.failure_classification.lower())
        self.assertNotIn("worker", adapter.calls)

    def test_repository_metadata_does_not_appear_in_the_patch(self):
        _, outcome = self.run_cycle()
        patch = self.artifact("diff.patch").read_text(encoding="utf-8")
        self.assertNotIn(".git/", patch)
        self.assertIn("evidence.txt", patch)


class UnverifiableDispatchTest(CycleTestCase):
    """The plane saying it cannot tell is not the plane saying it failed.

    Observed against a live runtime: with no credential in the image, Claude
    Code's turn never began and Orca reported `outcome_unknown` with
    `turn_start_unobserved` — explicitly unverifiable, not proof of failure.
    """

    def never_settles(self):
        return dict(dispatch_state="outcome_unknown", wait_settles=False)

    def test_an_unverifiable_dispatch_settles_unknown_rather_than_error(self):
        _, outcome = self.run_cycle(**self.never_settles())
        self.assertEqual(outcome.run_result.status, status.RunStatus.UNKNOWN)
        self.assertIn("unknown", outcome.run_result.failure_classification.lower())

    def test_a_dispatch_that_recovers_after_an_unobserved_start_settles(self):
        _, outcome = self.run_cycle(dispatch_state="outcome_unknown", wait_settles=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)
        self.assertEqual(outcome.run_result.worker.outcome, "DONE")
        self.assertEqual(outcome.run_result.check.exit_code, 0)

    def test_the_dispatch_identifiers_are_still_recorded(self):
        _, outcome = self.run_cycle(**self.never_settles())
        worker_record = outcome.run_result.worker
        self.assertEqual(worker_record.dispatch_id, "dispatch-fake-1")
        self.assertEqual(worker_record.outcome, "outcome_unknown")
        self.assertEqual(worker_record.model, "pinned-model")

    def test_the_evidence_is_still_exported_and_the_environment_destroyed(self):
        _, outcome = self.run_cycle(**self.never_settles())
        self.assertEqual(outcome.run_result.export.status, status.ExportStatus.CONFIRMED)
        self.assertEqual(outcome.run_result.cleanup.status, status.CleanupStatus.DESTROYED)

    def test_a_genuine_refusal_is_still_an_error(self):
        _, outcome = self.run_cycle(dispatch_state="refused")
        self.assertEqual(outcome.run_result.status, status.RunStatus.ERROR)


class BootstrapOrderTest(CycleTestCase):
    """Provisioning is what makes the environment able to do the rest.

    Observed on a real container: the repository initialisation ran first and
    failed with `executable file not found`, because git is installed by the
    provisioning steps that had not run yet.
    """

    def test_provisioning_runs_before_the_repository_is_initialised(self):
        document = self.make_config().to_document()
        document["distrobox"]["provision"] = [["mkdir", "-p", "/tmp/provisioned"]]
        adapter, outcome = self.run_cycle(config_document=document)
        bootstrap = outcome.run_result.phase("bootstrap")
        argv = [" ".join(c.argv) for c in bootstrap.commands]
        provisioned = next(i for i, line in enumerate(argv) if "provisioned" in line)
        initialised = next(i for i, line in enumerate(argv) if "git" in line and "init" in line)
        self.assertLess(provisioned, initialised, argv)

    def test_the_project_is_placed_before_either(self):
        adapter, _ = self.run_cycle()
        self.assertIn("put_tree", adapter.calls)
        first_git = next(
            i for i, c in enumerate(adapter.calls) if c.startswith("execute:git")
        )
        self.assertLess(adapter.calls.index("put_tree"), first_git)


class TerminalOutsideTheEnvironmentTest(CycleTestCase):
    """A command line inside an environment can still reach a runtime outside it."""

    def test_a_coordinator_terminal_on_another_machine_blocks_the_run(self):
        _, outcome = self.run_cycle(terminal_answers="workstation")
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("not in this environment", outcome.run_result.failure_classification)

    def test_nothing_is_dispatched_to_a_terminal_that_is_not_ours(self):
        adapter, _ = self.run_cycle(terminal_answers="workstation")
        self.assertNotIn("worker-start", " ".join(adapter.calls))


class AdmissionTest(CycleTestCase):
    """One environment per backend, and a run that left residue keeps its slot."""

    SECOND_RUN_ID = "20260914T221630Z-abc123-0123abce"

    def cycle(self, run_id, **adapter_options):
        run_config = self.make_config()
        adapter = FakeAdapter(
            self.state / run_id, host_project=self.repo, **adapter_options
        )
        return lifecycle.run_cycle(
            run_config=run_config,
            adapter=adapter,
            run_id=run_id,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )

    def leases(self):
        return admission.read_leases(self.state, "distrobox")

    def test_a_settled_run_takes_a_slot_and_gives_it_back(self):
        outcome = self.cycle(RUN_ID)
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)
        record = outcome.run_result.admission
        self.assertTrue(record.granted)
        self.assertTrue(record.released)
        self.assertEqual(self.leases(), [])

    def test_the_slot_is_recorded_as_an_artifact(self):
        self.cycle(RUN_ID)
        document = json.loads(
            self.artifact("admission.json").read_text(encoding="utf-8")
        )
        self.assertTrue(document["granted"])
        self.assertEqual(document["policy"]["effective_max_active"], 1)

    def test_a_run_that_left_residue_keeps_its_slot_and_blocks_the_next(self):
        first = self.cycle(RUN_ID, stop_confirmed=False)
        self.assertNotEqual(first.run_result.cleanup.status.value, "DESTROYED")
        self.assertFalse(first.run_result.admission.released)
        self.assertEqual([record.state for record in self.leases()], [admission.RETAINED])

        second = self.cycle(self.SECOND_RUN_ID)
        self.assertEqual(second.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("cleanup ended as", second.run_result.admission.detail
                      + " " + second.run_result.failure_classification)

    def test_a_refused_run_creates_nothing(self):
        self.cycle(RUN_ID, stop_confirmed=False)
        outcome = self.cycle(self.SECOND_RUN_ID)
        names = {
            phase.name: phase.status.value for phase in outcome.run_result.phases
        }
        self.assertEqual(names["create"], "SKIPPED")
        self.assertIsNone(outcome.run_result.environment_id)
        self.assertFalse((self.state / self.SECOND_RUN_ID).exists())


class HostRegistryTest(CycleTestCase):
    """An environment that registers itself into the operator's Orca is residue."""

    def profile_path(self):
        directory = self.host_home / ".config/orca/profiles/local-default"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "orca-data.json"

    def write_registry(self, repos):
        self.profile_path().write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "repos": [{"path": path} for path in repos],
                    "settings": {},
                }
            ),
            encoding="utf-8",
        )

    def test_a_clean_registry_leaves_the_run_settled(self):
        self.write_registry(["/home/someone/unrelated"])
        _, outcome = self.run_cycle()
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)
        registry = outcome.run_result.cleanup.host_registry
        self.assertTrue(registry["clean"], registry["reason"])

    def test_an_entry_naming_this_run_makes_the_cleanup_residue(self):
        """The box reached the host's application and registered its copy there."""

        class RegisteringAdapter(FakeAdapter):
            def __init__(inner, *args, profile, **options):
                super().__init__(*args, **options)
                inner.profile = profile

            def create(inner):
                handle = super().create()
                inner.profile.write_text(
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "repos": [{"path": handle.project_path}],
                            "settings": {},
                        }
                    ),
                    encoding="utf-8",
                )
                return handle

        self.write_registry([])
        run_config = self.make_config()
        adapter = RegisteringAdapter(
            self.state / RUN_ID, host_project=self.repo, profile=self.profile_path()
        )
        outcome = lifecycle.run_cycle(
            run_config=run_config,
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )
        self.assertEqual(
            outcome.run_result.cleanup.status.value,
            "RESIDUE",
            outcome.run_result.cleanup.reason,
        )
        self.assertNotEqual(outcome.run_result.status, status.RunStatus.SETTLED)
        self.assertIn("operator's own Orca", outcome.run_result.cleanup.reason)
        self.assertIn("their decision", outcome.run_result.cleanup.reason)

    def test_that_residue_keeps_the_admission_slot(self):
        self.write_registry([])
        outcome = None

        class RegisteringAdapter(FakeAdapter):
            def create(inner):
                handle = super().create()
                (self.host_home / ".config/orca/profiles/local-default"
                 / "orca-data.json").write_text(
                    json.dumps({"repos": [{"path": handle.home_path}], "settings": {}}),
                    encoding="utf-8",
                )
                return handle

        outcome = lifecycle.run_cycle(
            run_config=self.make_config(),
            adapter=RegisteringAdapter(self.state / RUN_ID, host_project=self.repo),
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )
        self.assertFalse(outcome.run_result.admission.released)
        self.assertEqual(
            [record.state for record in admission.read_leases(self.state, "distrobox")],
            [admission.RETAINED],
        )


SKILL_DIGEST = "f7da0dd40d8681e2b0303fa0fa2f7ee4e36f2eca6495a4af57b92b9857dff732"
PLUGIN_COMMIT = "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"


class SkillsGateTest(CycleTestCase):
    """An image that says it installed a skill is not an agent that has it."""

    def make_config(self, **overrides):
        document = json.loads(
            json.dumps(
                {
                    "skills": {
                        "required": True,
                        "bundled": [{"name": "orchestration", "sha256": SKILL_DIGEST}],
                        "plugins": [
                            {
                                "name": "superpowers",
                                "path": "/opt/dely-cycle/superpowers",
                                "revision": PLUGIN_COMMIT,
                            }
                        ],
                    }
                }
            )
        )
        document.update(overrides)
        return super().make_config(**document)

    def cycle(self, **adapter_options):
        adapter = FakeAdapter(
            self.state / RUN_ID, host_project=self.repo, **adapter_options
        )
        return lifecycle.run_cycle(
            run_config=self.make_config(),
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )

    def everything_present(self):
        return {
            "skill_answers": {"orchestration": ("/h/.agents/skills/orchestration/SKILL.md", SKILL_DIGEST)},
            "plugin_answers": {"superpowers": (PLUGIN_COMMIT, "/opt/dely-cycle/superpowers")},
            "plugin_installed": {"superpowers": (14, 14)},
        }

    def test_the_pinned_skills_being_there_lets_the_run_continue(self):
        outcome = self.cycle(**self.everything_present())
        self.assertEqual(outcome.run_result.skills.status.value, "OK")
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)

    def test_an_absent_skill_blocks_before_the_task(self):
        answers = self.everything_present()
        answers["skill_answers"] = {"orchestration": ("missing", "")}
        outcome = self.cycle(**answers)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertEqual(
            outcome.run_result.phase("task").status.value, "SKIPPED"
        )
        self.assertIn("orchestration", outcome.run_result.skills.detail)

    def test_a_skill_that_is_not_the_pinned_one_blocks(self):
        answers = self.everything_present()
        answers["skill_answers"] = {"orchestration": ("/h/SKILL.md", "0" * 64)}
        outcome = self.cycle(**answers)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("drifted", outcome.run_result.skills.detail)

    def test_a_plugin_at_the_wrong_commit_blocks(self):
        answers = self.everything_present()
        answers["plugin_answers"] = {"superpowers": ("0" * 40, "/opt/dely-cycle/superpowers")}
        outcome = self.cycle(**answers)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("different commit", outcome.run_result.skills.detail)

    def test_the_findings_are_exported(self):
        outcome = self.cycle(**self.everything_present())
        document = json.loads(self.artifact("skills.json").read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(entry["name"] for entry in document["findings"]),
            ["orchestration", "superpowers"],
        )


class HandoffTest(CycleTestCase):
    """Control hands the diff to a reviewer that did not write it."""

    def cycle(self, **adapter_options):
        adapter = FakeAdapter(
            self.state / RUN_ID, host_project=self.repo, **adapter_options
        )
        return adapter, lifecycle.run_cycle(
            run_config=self.make_config(),
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )

    def test_the_review_is_a_second_dispatch_in_the_same_run(self):
        _, outcome = self.cycle()
        result = outcome.run_result
        self.assertEqual(result.review.status.value, "OK", result.review.detail)
        self.assertNotEqual(result.worker.dispatch_id, result.reviewer.dispatch_id)
        self.assertNotEqual(result.worker.terminal, result.reviewer.terminal)
        self.assertEqual(result.worker.run_id, result.reviewer.run_id)
        self.assertEqual(result.reviewer.role, "reviewer")

    def test_the_reviewer_answers_about_the_implementers_diff(self):
        _, outcome = self.cycle()
        review_record = outcome.run_result.review
        self.assertTrue(review_record.same_diff, review_record.same_diff_detail)
        self.assertEqual(review_record.diff_sha256, review_record.diff_sha256_after)
        self.assertEqual(
            review_record.diff_sha256, review_record.diff_reported_by_reviewer
        )
        self.assertGreater(review_record.diff_lines, 0)
        self.assertEqual(review_record.verdict, "accept")

    def test_a_reviewer_that_read_another_diff_fails_the_run(self):
        _, outcome = self.cycle(reviewer_reads_another_diff=True)
        self.assertEqual(outcome.run_result.review.status.value, "FAILED")
        self.assertIn("different diff", outcome.run_result.review.detail)
        self.assertNotEqual(outcome.run_result.status, status.RunStatus.SETTLED)

    def test_one_dispatch_answering_twice_fails_the_run(self):
        _, outcome = self.cycle(one_dispatch_for_both=True)
        self.assertEqual(outcome.run_result.review.status.value, "FAILED")
        self.assertIn("not independent", outcome.run_result.review.detail)

    def test_a_reviewer_that_wrote_no_verdict_fails_the_run(self):
        _, outcome = self.cycle(review_verdict=None)
        self.assertEqual(outcome.run_result.review.status.value, "FAILED")
        self.assertIn("did not report", outcome.run_result.review.detail)

    def test_the_handed_over_diff_and_the_verdict_are_exported(self):
        _, outcome = self.cycle()
        patch = self.artifact("handoff-diff.patch").read_text(encoding="utf-8")
        self.assertIn("evidence.txt", patch)
        document = json.loads(self.artifact("review.json").read_text(encoding="utf-8"))
        self.assertEqual(document["verdict"], "accept")
        self.assertTrue(document["separation"]["ok"])

    def test_the_two_agents_replies_are_kept_apart(self):
        _, outcome = self.cycle()
        self.assertTrue(self.artifact("dispatch/worker-start.stdout").is_file())
        self.assertTrue(
            self.artifact("dispatch/reviewer-worker-start.stdout").is_file()
        )

    def test_a_configuration_that_asks_for_no_review_skips_it(self):
        document = json.loads(json.dumps({"review": {"enabled": False}}))
        run_config = super(HandoffTest, self).make_config(**document)
        adapter = FakeAdapter(self.state / RUN_ID, host_project=self.repo)
        outcome = lifecycle.run_cycle(
            run_config=run_config,
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)
        self.assertEqual(outcome.run_result.phase("review").status.value, "SKIPPED")


class DisplayGateTest(CycleTestCase):
    """The run has to show its application went to its own screen."""

    def cycle(self, **adapter_options):
        adapter = FakeAdapter(
            self.state / RUN_ID, host_project=self.repo, **adapter_options
        )
        return lifecycle.run_cycle(
            run_config=self.make_config(),
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )

    def test_a_window_appearing_on_this_runs_screen_lets_it_continue(self):
        outcome = self.cycle()
        record = outcome.run_result.display
        self.assertEqual(record.status.value, "OK", record.detail)
        self.assertEqual([w["id"] for w in record.appeared], ["2000"])
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)

    def test_no_window_anywhere_blocks_before_the_task(self):
        outcome = self.cycle(window_appears=False)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertEqual(outcome.run_result.phase("task").status.value, "SKIPPED")
        self.assertIn("nothing here says where", outcome.run_result.display.detail)

    def test_a_screen_that_does_not_answer_blocks(self):
        outcome = self.cycle(display_unreachable=True)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("did not answer", outcome.run_result.display.detail)

    def test_the_screen_is_recorded_as_an_artifact(self):
        self.cycle()
        document = json.loads(self.artifact("display.json").read_text(encoding="utf-8"))
        self.assertEqual(document["mode"], "virtual")
        self.assertEqual(document["display"], ":0")
        self.assertTrue(document["reachable"])

    def test_a_blocked_display_still_cleans_up(self):
        outcome = self.cycle(window_appears=False)
        self.assertEqual(outcome.run_result.cleanup.status.value, "DESTROYED")


class PluginSkillsReachTheAgentTest(SkillsGateTest):
    """A checkout at the pinned commit whose skills the agent cannot read."""

    def test_skills_that_never_reached_the_agent_block_the_run(self):
        answers = self.everything_present()
        answers["plugin_installed"] = {"superpowers": (14, 2)}
        outcome = self.cycle(**answers)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("2 of this checkout's 14 skills", outcome.run_result.skills.detail)


class AuthWorksTest(CycleTestCase):
    """The bootstrap copied something; whether it works is a separate question."""

    def cycle(self, **adapter_options):
        adapter = FakeAdapter(
            self.state / RUN_ID, host_project=self.repo, **adapter_options
        )
        return lifecycle.run_cycle(
            run_config=self.make_config(),
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )

    def test_a_signed_in_agent_is_recorded_beside_the_bootstrap(self):
        outcome = self.cycle()
        record = outcome.run_result.auth
        self.assertTrue(record.verified)
        self.assertIn("signed in through claude.ai", record.verify_detail)
        self.assertNotEqual(record.detail, record.verify_detail)
        self.assertEqual(outcome.run_result.status, status.RunStatus.SETTLED)

    def test_the_receipt_names_no_person_and_no_organisation(self):
        self.cycle()
        raw = self.artifact("auth-receipt.json").read_text(encoding="utf-8")
        for absent in ("email", "orgName", "orgId", "example.invalid"):
            self.assertNotIn(absent, raw)

    def test_an_agent_that_is_not_signed_in_blocks_the_run(self):
        outcome = self.cycle(signed_in=False)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertEqual(outcome.run_result.phase("task").status.value, "SKIPPED")
        self.assertIn("expired", outcome.run_result.failure_classification)

    def test_an_agent_that_gives_no_answer_blocks_the_run(self):
        outcome = self.cycle(signed_in=None)
        self.assertEqual(outcome.run_result.status, status.RunStatus.BLOCKED)
        self.assertIn("no readable answer", outcome.run_result.auth.verify_detail)


class AcknowledgedDeliveryTest(CycleTestCase):
    """A bound Run replays a delivery until it is acknowledged."""

    def cycle(self):
        adapter = FakeAdapter(self.state / RUN_ID, host_project=self.repo)
        return adapter, lifecycle.run_cycle(
            run_config=self.make_config(),
            adapter=adapter,
            run_id=RUN_ID,
            host_home=self.host_home,
            environ={},
            tool_versions={"runner": "one"},
        )

    def test_the_reviewer_waits_on_its_own_message_not_the_implementers(self):
        adapter, outcome = self.cycle()
        self.assertNotIn("replayed-delivery", adapter.calls)
        self.assertNotEqual(
            outcome.run_result.worker.delivery_id,
            outcome.run_result.reviewer.delivery_id,
        )

    def test_the_reviewers_wait_acknowledges_the_batch_before_it(self):
        _, outcome = self.cycle()
        waits = [
            command
            for command in outcome.run_result.reviewer.commands
            if "--wait" in command.argv
        ]
        self.assertTrue(waits, "the reviewer never waited")
        self.assertIn("--ack", waits[0].argv)
        self.assertIn(outcome.run_result.worker.delivery_id, waits[0].argv)

    def test_the_implementers_own_wait_acknowledges_nothing(self):
        _, outcome = self.cycle()
        waits = [
            command
            for command in outcome.run_result.worker.commands
            if "--wait" in command.argv
        ]
        self.assertNotIn("--ack", waits[0].argv)
