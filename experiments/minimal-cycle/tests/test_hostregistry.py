"""The environment must not register itself into the operator's own Orca."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from cycle_runner import hostregistry

RUN_ID = "20260916T120000Z-abcdef-00000001"
RUN_PATH = f"/var/tmp/dely-cycle/state/{RUN_ID}"
OTHER_RUN = "20260916T110000Z-abcdef-00000000"

MARKERS = (RUN_ID, RUN_PATH, f"dely-cycle-{RUN_ID.lower()}")


def profile(repos):
    return {
        "schemaVersion": 1,
        "repos": [{"id": str(index), "path": path} for index, path in enumerate(repos)],
        "projects": [],
        "worktreeMeta": {},
        "settings": {"theme": "dark"},
        "ui": {},
    }


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.profile_dir = self.home / ".config/orca/profiles/local-default"
        self.profile_dir.mkdir(parents=True)

    def write_profile(self, repos=()):
        path = self.profile_dir / "orca-data.json"
        path.write_text(json.dumps(profile(repos)), encoding="utf-8")
        return path

    def fingerprint(self, markers=MARKERS):
        return hostregistry.fingerprint(self.home, markers=markers)


class CleanHostTest(RegistryTestCase):
    def test_a_registry_that_never_heard_of_this_run_is_clean(self):
        self.write_profile(["/home/someone/work/project"])
        clean, reason = hostregistry.verdict(self.fingerprint())
        self.assertTrue(clean, reason)
        self.assertIn("names this run", reason)

    def test_an_entry_for_a_different_run_does_not_implicate_this_one(self):
        self.write_profile([f"/var/tmp/dely-cycle/state/{OTHER_RUN}/home/project"])
        clean, reason = hostregistry.verdict(self.fingerprint())
        self.assertTrue(clean, reason)

    def test_the_operators_own_scrollback_is_not_a_registration(self):
        """The run prints its identifier; the terminal it printed into keeps it.

        That is somebody watching their run. Treating it as a registration
        would make every run started from an Orca terminal report residue.
        """
        self.write_profile([])
        history = self.home / ".config/orca/terminal-history/some-terminal"
        history.mkdir(parents=True)
        (history / "output.log").write_text(
            f"run_id:    {RUN_ID}\nartifacts: {RUN_PATH}\n", encoding="utf-8"
        )
        clean, reason = hostregistry.verdict(self.fingerprint())
        self.assertTrue(clean, reason)

    def test_the_record_that_a_terminal_exists_is_still_a_registration(self):
        """Beside the scrollback sits what worktree the terminal belongs to."""
        self.write_profile([])
        history = self.home / ".config/orca/terminal-history/some-terminal"
        history.mkdir(parents=True)
        (history / "meta.json").write_text(
            json.dumps({"worktreeId": f"abc::{RUN_PATH}/home/project"}), encoding="utf-8"
        )
        clean, reason = hostregistry.verdict(self.fingerprint())
        self.assertFalse(clean, reason)
        self.assertIn("meta.json", reason)

    def test_a_host_with_no_orca_at_all_is_clean(self):
        clean, reason = hostregistry.verdict(
            hostregistry.fingerprint(self.home / "nowhere", markers=MARKERS)
        )
        self.assertTrue(clean, reason)

    def test_the_collections_it_counts_are_read(self):
        self.write_profile(["/home/someone/work/project"])
        sizes = self.fingerprint()["collection_sizes"]
        self.assertEqual(sizes["repos"], 1)
        self.assertEqual(sizes["settings"], 1)


class ContaminatedHostTest(RegistryTestCase):
    def test_a_repository_entry_naming_this_run_is_not_clean(self):
        self.write_profile([f"{RUN_PATH}/home/project"])
        clean, reason = hostregistry.verdict(self.fingerprint())
        self.assertFalse(clean)
        self.assertIn("orca-data.json", reason)
        self.assertIn("operator's own Orca", reason)

    def test_the_container_name_is_searched_for_too(self):
        self.write_profile([])
        (self.profile_dir / "terminal-meta.json").write_text(
            json.dumps({"box": f"dely-cycle-{RUN_ID.lower()}"}), encoding="utf-8"
        )
        # A file beside the profile is part of the registry tree.
        clean, _ = hostregistry.verdict(self.fingerprint())
        self.assertFalse(clean)

    def test_the_orchestration_store_is_searched_as_bytes(self):
        self.write_profile([])
        store = self.home / ".config/orca/orchestration.db"
        store.write_bytes(b"SQLite format 3\x00" + RUN_ID.encode() + b"\x00\x01\x02")
        fingerprint = self.fingerprint()
        clean, reason = hostregistry.verdict(fingerprint)
        self.assertFalse(clean)
        self.assertIn("orchestration.db", reason)


class FailsClosedTest(RegistryTestCase):
    def test_searching_for_nothing_proves_nothing(self):
        self.write_profile([])
        clean, reason = hostregistry.verdict(self.fingerprint(markers=()))
        self.assertFalse(clean)
        self.assertIn("searched for nothing", reason)

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_a_registry_file_that_cannot_be_read_is_not_a_clean_one(self):
        path = self.write_profile([])
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o600)
        clean, reason = hostregistry.verdict(self.fingerprint())
        self.assertFalse(clean)
        self.assertIn("could not be read", reason)


class DifferenceTest(RegistryTestCase):
    def test_a_collection_that_grew_is_reported(self):
        self.write_profile(["/home/someone/one"])
        before = self.fingerprint()
        self.write_profile(["/home/someone/one", "/home/someone/two"])
        after = self.fingerprint()
        changed = hostregistry.difference(before, after)["collections_changed"]
        self.assertEqual(changed["repos"], {"before": 1, "after": 2})


if __name__ == "__main__":
    unittest.main()
