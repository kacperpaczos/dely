"""Ending what a run that was killed left running on this host.

Cleanup is the end of the cycle, so it only runs for a run that reached its
end. A run killed in the middle of one reaches no end, and what it started
keeps running: a container, an agent inside it with the host home mounted, a
whole application runtime, with no runner and no supervisor left to stop them.
`residue` found them and could only describe them — it refuses to remove state
while processes hold it, which is right, and it left no way to stop them.

These are the instruments for the command that does. Almost all of them are
refusals, because the failure this command can cause is worse than the one it
exists to fix: a stop that reached for the operator's own application would be
the same accident, made deliberate.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cycle_runner import admission, cli, processes, status
from cycle_runner.adapters.base import (
    BackendAdapter,
    DestroyReport,
    EnvironmentHandle,
    PreflightReport,
    Resource,
    StopReport,
)
from tests.test_config import minimal_document
from tests.test_lifecycle import make_source_repo

RUN = "20260918T120000Z-abcdef-00000001"
OTHER_RUN = "20260918T120500Z-abcdef-00000002"


class StubAdapter(BackendAdapter):
    """A backend that answers about one box without touching this machine."""

    name = "distrobox"
    shares_host_processes = True

    def __init__(self, *, container: str, run_state: Path, observable=True):
        self.container = container
        self.run_state = run_state
        self.observable = observable
        self.present = True
        self.removals = 0

    def preflight(self) -> PreflightReport:
        return PreflightReport(backend=self.name)

    def create(self) -> EnvironmentHandle:
        raise AssertionError("nothing here creates an environment")

    def execute(self, argv, *, timeout, cwd=None, env=None, extra_values=()):
        raise AssertionError("nothing here runs a command in an environment")

    def put_tree(self, local_dir, remote_dir):
        raise AssertionError("nothing here moves a tree")

    def fetch_tree(self, remote_dir, local_dir):
        raise AssertionError("nothing here moves a tree")

    def write_file(self, remote_path, content, *, mode=0o600):
        raise AssertionError("nothing here writes into an environment")

    def stop(self) -> StopReport:
        raise AssertionError("nothing here stops an environment the polite way")

    def destroy(self) -> DestroyReport:
        raise AssertionError(
            "a killed run's state is not destroyed with its environment"
        )

    def plan_handle(self) -> EnvironmentHandle:
        return EnvironmentHandle(
            environment_id=self.container,
            home_path=str(self.run_state / "home"),
            project_path=str(self.run_state / "home" / "project"),
            per_run_resources=(
                Resource(kind="container", identifier=self.container),
                Resource(kind="path", identifier=str(self.run_state)),
            ),
        )

    def can_see_environment(self) -> tuple[bool, str]:
        if not self.observable:
            return False, "no container manager is on this host's path"
        return True, "distrobox over podman lists the boxes on this host"

    def resource_exists(self, resource: Resource) -> bool:
        if resource.kind == "path":
            return Path(resource.identifier).exists()
        return resource.kind == "container" and self.present

    def remove_environment(self) -> DestroyReport:
        self.removals += 1
        self.present = False
        return DestroyReport(
            removed=(f"container:{self.container}",),
            retained=(f"path:{self.run_state}",),
            detail=f"{self.container} is gone; {self.run_state} was left standing",
        )


def survey_that_finds(report: processes.SurveyReport):
    """A stand-in survey that records the paths it was asked about."""
    asked: list[list[str]] = []

    def survey(paths, **_options):
        asked.append(list(paths))
        return report

    survey.asked = asked
    return survey


def stopped(pids, attribution):
    return processes.SurveyReport(
        found=list(pids),
        stopped=list(pids),
        attribution=dict(attribution),
        detail=f"{len(pids)} process(es) the run had started were stopped",
    )


class StopCommandTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.repo = make_source_repo(self.root / "src")
        self.run_state = self.state / RUN
        (self.run_state / "home" / ".claude").mkdir(parents=True)
        (self.run_state / "home" / ".claude" / "settings.json").write_text(
            "{}", encoding="utf-8"
        )
        (self.run_state / "distrobox.ini").write_text("[box]\n", encoding="utf-8")
        self.adapter = StubAdapter(
            container=f"dely-cycle-{RUN}", run_state=self.run_state
        )

    def write_config(self):
        document = minimal_document()
        document["artifact_root"] = str(self.root / "artifacts")
        document["state_root"] = str(self.state)
        document["project"]["source"] = str(self.repo)
        document["project"]["revision"] = "main"
        path = self.root / "distrobox.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def invoke(self, run_id=RUN, *, survey=None, config=None):
        arguments = cli.build_parser().parse_args(
            ["stop", "--config", str(config or self.write_config()), "--run-id", run_id]
        )
        out, err = io.StringIO(), io.StringIO()
        code = cli._stop(
            arguments,
            out,
            err,
            survey=survey or survey_that_finds(processes.SurveyReport()),
            adapter_for=lambda *_arguments: self.adapter,
        )
        return code, out.getvalue(), err.getvalue()

    @property
    def blocked(self):
        return status.exit_code(status.RunStatus.BLOCKED)


class OneNamedDeadRunTest(StopCommandTestCase):
    def test_a_name_this_runner_never_minted_is_refused(self):
        code, _, err = self.invoke("all")
        self.assertEqual(code, self.blocked)
        self.assertIn("not a run identifier", err)
        self.assertTrue(self.adapter.present)

    def test_a_lease_with_a_live_owner_means_this_is_not_a_dead_run(self):
        admission.acquire(
            root=self.state,
            run_id=RUN,
            backend="distrobox",
            limits=admission.Limits(),
            claim=admission.Claim(),
            pid=os.getpid(),
            wait_seconds=0.5,
            sleeper=lambda _: None,
        )
        survey = survey_that_finds(stopped([4242], {4242: "its home names it"}))
        code, _, err = self.invoke(survey=survey)
        self.assertEqual(code, self.blocked)
        self.assertIn("is not a dead run", err)
        self.assertEqual(survey.asked, [])
        self.assertTrue(self.adapter.present)

    def test_a_question_this_host_could_not_ask_stops_it_before_it_signals(self):
        self.adapter.observable = False
        survey = survey_that_finds(stopped([4242], {4242: "its home names it"}))
        code, _, err = self.invoke(survey=survey)
        self.assertEqual(code, self.blocked)
        self.assertIn("could not be asked", err)
        self.assertIn("nothing was signalled", err)
        self.assertEqual(survey.asked, [])

    def test_only_the_named_run_s_own_paths_are_ever_surveyed(self):
        survey = survey_that_finds(processes.SurveyReport())
        self.invoke(survey=survey)
        self.assertEqual(
            survey.asked,
            [[str(self.state / RUN), str(self.root / "artifacts" / RUN)]],
        )
        self.assertNotIn(OTHER_RUN, "".join(survey.asked[0]))


class UnattributedProcessTest(StopCommandTestCase):
    """A process it cannot attribute is a refusal, not a target and not a skip."""

    def report(self):
        return processes.SurveyReport(
            found=[4242],
            attribution={4242: "its home names this run"},
            unattributed=[4243],
            unreadable={
                4243: (
                    "its parent pid 4242 is this run's, and this host would not "
                    "let this survey read its environment"
                )
            },
            detail=(
                "1 process(es) near this run could not be established to be its "
                "own or to be somebody else's, so nothing was signalled"
            ),
        )

    def test_it_refuses_and_says_which_process_and_why(self):
        code, out, err = self.invoke(survey=survey_that_finds(self.report()))
        self.assertEqual(code, self.blocked)
        self.assertIn("pid 4243", out)
        self.assertIn("would not let this survey read its environment", out)
        self.assertIn("nothing was signalled", err)

    def test_the_environment_is_not_removed_over_a_process_nobody_could_place(self):
        self.invoke(survey=survey_that_finds(self.report()))
        self.assertEqual(self.adapter.removals, 0)
        self.assertTrue(self.adapter.present)

    def test_what_it_would_have_signalled_is_still_named(self):
        _, out, _ = self.invoke(survey=survey_that_finds(self.report()))
        self.assertIn("pid 4242: its home names this run", out)


class SurvivingProcessTest(StopCommandTestCase):
    def report(self):
        return processes.SurveyReport(
            found=[4242],
            surviving=[4242],
            attribution={4242: "its home names this run"},
            refused={4242: "PermissionError: [Errno 1] Operation not permitted"},
            detail="1 process(es) the run had started would not stop",
        )

    def test_the_environment_stays_while_something_of_the_run_is_running(self):
        code, _, err = self.invoke(survey=survey_that_finds(self.report()))
        self.assertEqual(code, self.blocked)
        self.assertEqual(self.adapter.removals, 0)
        self.assertTrue(self.adapter.present)
        self.assertIn("left standing", err)

    def test_a_signal_the_host_refused_is_reported_as_what_it_was(self):
        _, out, _ = self.invoke(survey=survey_that_finds(self.report()))
        self.assertIn("still here:  pid 4242: PermissionError", out)


class StoppedAndRemovedTest(StopCommandTestCase):
    def test_what_it_stopped_and_what_it_removed_are_both_reported(self):
        survey = survey_that_finds(
            stopped([4242, 4243], {4242: "its home names this run", 4243: "in its box"})
        )
        code, out, err = self.invoke(survey=survey)
        self.assertEqual(code, 0, err)
        self.assertIn("attributed:  pid 4242: its home names this run", out)
        self.assertIn("stopped:     [4242, 4243]", out)
        self.assertIn(f"removed:     container:dely-cycle-{RUN}", out)
        self.assertEqual(self.adapter.removals, 1)
        self.assertFalse(self.adapter.present)

    def test_the_state_that_run_left_is_not_what_this_removes(self):
        code, out, err = self.invoke(
            survey=survey_that_finds(stopped([4242], {4242: "its home names it"}))
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((self.run_state / "home" / ".claude" / "settings.json").is_file())
        self.assertIn(f"--run-id {RUN} --discard", out)

    def test_a_dead_run_with_nothing_still_running_still_loses_its_box(self):
        code, out, err = self.invoke()
        self.assertEqual(code, 0, err)
        self.assertIn("nothing was running", out)
        self.assertEqual(self.adapter.removals, 1)

    def test_an_environment_that_outlives_its_removal_is_reported_as_present(self):
        class Stubborn(StubAdapter):
            def remove_environment(self):
                self.removals += 1
                return DestroyReport(
                    retained=(f"container:{self.container}",),
                    detail="distrobox rm --force returned and the box is still listed",
                )

        self.adapter = Stubborn(container=f"dely-cycle-{RUN}", run_state=self.run_state)
        code, _, err = self.invoke(
            survey=survey_that_finds(stopped([4242], {4242: "its home names it"}))
        )
        self.assertEqual(code, self.blocked)
        self.assertIn("is still on this host", err)


if __name__ == "__main__":
    unittest.main()
