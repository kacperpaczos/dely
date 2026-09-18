"""A fresh home has never run any of this, and both applications ask first."""

import json
import tempfile
import unittest
from pathlib import Path

from cycle_runner import config as config_module, firstrun
from tests.fakes import FakeAdapter
from tests.test_config import minimal_document


def make_config(auth_section=None):
    document = minimal_document()
    if auth_section is not None:
        document["auth"] = auth_section
    return config_module.from_document(document)


class FirstRunCase(unittest.TestCase):
    """The shared fixture: one fake environment, one application of the state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.adapter = FakeAdapter(self.root / "run")
        self.handle = self.adapter.create()
        self.config = make_config()
        self.addCleanup(self._tmp.cleanup)

    def apply(self):
        return firstrun.apply(
            run_config=self.config, adapter=self.adapter, handle=self.handle
        )

    def state(self):
        return json.loads(
            (Path(self.handle.home_path) / firstrun.STATE_RELATIVE).read_text()
        )

    def settings(self):
        return json.loads(
            (Path(self.handle.home_path) / firstrun.SETTINGS_RELATIVE).read_text()
        )

    def profile(self):
        return json.loads(
            (Path(self.handle.home_path) / firstrun.PROFILE_RELATIVE).read_text()
        )


class FirstRunStateTest(FirstRunCase):
    """What the agent asks a home it has never run in."""

    def test_onboarding_is_answered_so_the_agent_does_not_ask_to_sign_in(self):
        self.apply()
        self.assertTrue(self.state()["hasCompletedOnboarding"])

    def test_the_project_copy_is_trusted_by_its_path_inside_the_environment(self):
        self.apply()
        entry = self.state()["projects"][self.handle.project_path]
        self.assertTrue(entry["hasTrustDialogAccepted"])

    def test_the_external_imports_this_project_declares_are_answered(self):
        self.apply()
        entry = self.state()["projects"][self.handle.project_path]
        self.assertTrue(entry["hasClaudeMdExternalIncludesApproved"])

    def test_the_permission_mode_warning_is_answered_in_the_settings_file(self):
        self.apply()
        self.assertTrue(self.settings()["skipDangerousModePermissionPrompt"])

    def test_the_state_is_written_rather_than_copied_from_the_host(self):
        record = self.apply()
        rendered = json.dumps(self.state()) + json.dumps(record.to_document())
        self.assertNotIn(str(Path.home()), rendered)
        for key in ("oauthAccount", "userID", "machineID", "history"):
            self.assertNotIn(key, rendered)

    def test_the_receipt_names_the_files_and_the_questions_they_answer(self):
        record = self.apply()
        self.assertEqual(
            record.entries,
            [
                firstrun.STATE_RELATIVE,
                firstrun.SETTINGS_RELATIVE,
                firstrun.PROFILE_RELATIVE,
            ],
        )
        self.assertTrue(record.questions)

    def test_an_api_key_helper_keeps_its_settings_entry(self):
        self.config = make_config(
            {
                "mode": "api_key_helper",
                "reference": "workshop-helper",
                "helper_argv": ["/usr/local/bin/key-helper"],
            }
        )
        self.apply()
        settings = self.settings()
        self.assertEqual(settings["apiKeyHelper"], "/usr/local/bin/key-helper")
        self.assertTrue(settings["skipDangerousModePermissionPrompt"])


class OrcaFirstRunTest(FirstRunCase):
    """The application's own wizard, which nothing in the dispatch can dismiss.

    It does not block orchestration: a complete two-agent cycle was observed
    running from behind it. What it blocks is looking — every frame of that
    settled run photographed the wizard instead of the panels.
    """

    def test_the_flow_is_closed_because_that_is_what_shows_the_wizard(self):
        self.apply()
        closed_at = self.profile()["onboarding"]["closedAt"]
        self.assertIsInstance(closed_at, int)
        self.assertGreater(closed_at, 0)

    def test_the_seed_is_what_the_application_writes_when_the_flow_finishes(self):
        self.apply()
        onboarding = self.profile()["onboarding"]
        self.assertEqual(onboarding["flowVersion"], 4)
        self.assertEqual(onboarding["outcome"], "completed")
        self.assertEqual(onboarding["lastCompletedStep"], 5)

    def test_the_checklist_is_left_to_the_defaults_rather_than_invented(self):
        self.apply()
        self.assertNotIn("checklist", self.profile()["onboarding"])

    def test_the_seed_lands_in_the_profile_the_application_reads(self):
        self.apply()
        self.assertEqual(
            firstrun.PROFILE_RELATIVE,
            ".config/orca/profiles/local-default/orca-data.json",
        )
        self.assertTrue(
            (Path(self.handle.home_path) / firstrun.PROFILE_RELATIVE).is_file()
        )

    def test_the_seed_is_inside_the_per_run_home_and_owner_only(self):
        self.apply()
        seed = Path(self.handle.home_path) / firstrun.PROFILE_RELATIVE
        self.assertTrue(seed.is_relative_to(Path(self.handle.home_path)))
        self.assertEqual(seed.stat().st_mode & 0o777, 0o600)

    def test_the_receipt_names_the_wizard_among_the_questions(self):
        record = self.apply()
        self.assertIn(firstrun.PROFILE_RELATIVE, record.entries)
        self.assertTrue(
            any("wizard" in question for question in record.questions),
            record.questions,
        )
        self.assertEqual(len(record.questions), 5)

    def test_every_value_is_a_constant_or_this_runs_own_moment(self):
        """Nothing here is copied from a profile, so nothing here can leak one."""
        document = firstrun.onboarding_document(closed_at_ms=1_700_000_000_000)
        self.assertEqual(
            document,
            {
                "onboarding": {
                    "flowVersion": 4,
                    "closedAt": 1_700_000_000_000,
                    "outcome": "completed",
                    "lastCompletedStep": 5,
                }
            },
        )

    def test_the_seed_carries_nothing_of_the_host(self):
        self.apply()
        rendered = json.dumps(self.profile())
        self.assertNotIn(str(Path.home()), rendered)
        for key in ("repos", "projects", "worktreeMeta", "sshTargets", "userId"):
            self.assertNotIn(key, rendered)


if __name__ == "__main__":
    unittest.main()
