"""The shipped configurations are meant to be run, not edited first.

A placeholder in an example is the difference between "one documented command"
and "one documented command plus whatever the reader guesses". These tests are
what stop one reappearing, and what stops an example drifting away from the
versions the host is pinned to.
"""

import json
import re
import unittest
from pathlib import Path

from cycle_runner import config as config_module

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted(ROOT.glob("config.*.example.yaml"))
VERSIONS = json.loads((ROOT / "host" / "versions.json").read_text(encoding="utf-8"))

#: Anything shaped like "fill this in yourself".
PLACEHOLDER = re.compile(
    r"replace[-_ ]with|<your|your[-_]key|TODO|FIXME|CHANGEME|xxxx", re.IGNORECASE
)


class ExamplesExistTest(unittest.TestCase):
    def test_there_is_an_example_for_each_backend(self):
        self.assertEqual(
            sorted(path.name for path in EXAMPLES),
            ["config.distrobox.example.yaml", "config.vm.example.yaml"],
        )


class NoPlaceholdersTest(unittest.TestCase):
    def test_no_example_asks_the_reader_to_fill_something_in(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                found = PLACEHOLDER.findall(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    found, [], f"{path.name} still carries a placeholder: {found}"
                )

    def test_every_example_loads_as_a_configuration(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                config_module.load(path)


class PinsAgreeWithTheHostTest(unittest.TestCase):
    """An example that names a different version than the host was prepared to."""

    def configs(self):
        return [(path.name, config_module.load(path)) for path in EXAMPLES]

    def test_the_claude_code_version_is_the_pinned_one(self):
        pinned = VERSIONS["tool_image"]["contents"]["claude_code"]["version"]
        for name, run_config in self.configs():
            with self.subTest(example=name):
                self.assertEqual(run_config.claude_code_version, pinned)

    def test_the_orca_version_is_the_pinned_one(self):
        pinned = VERSIONS["tool_image"]["contents"]["orca"]["version"]
        for name, run_config in self.configs():
            with self.subTest(example=name):
                self.assertEqual(run_config.orca.version, pinned)

    def test_the_project_source_is_the_one_prepare_host_maintains(self):
        expected = Path(VERSIONS["project_source"]["path"])
        for name, run_config in self.configs():
            with self.subTest(example=name):
                self.assertEqual(run_config.project.source, expected)

    def test_every_pinned_skill_appears_in_every_example(self):
        expected = {
            entry["name"]: entry["skill_md_sha256"]
            for entry in VERSIONS["skills"]["bundled"]["entries"]
        }
        for name, run_config in self.configs():
            with self.subTest(example=name):
                self.assertEqual(
                    {item.name: item.sha256 for item in run_config.skills.bundled},
                    expected,
                )

    def test_every_pinned_plugin_appears_at_the_pinned_commit(self):
        expected = {
            entry["name"]: entry["revision"] for entry in VERSIONS["skills"]["plugins"]
        }
        for name, run_config in self.configs():
            with self.subTest(example=name):
                self.assertEqual(
                    {item.name: item.revision for item in run_config.skills.plugins},
                    expected,
                )


class SafeByDefaultTest(unittest.TestCase):
    def test_no_example_opts_into_parallel_runs(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                run_config = config_module.load(path)
                self.assertFalse(run_config.limits.parallel)
                self.assertEqual(run_config.limits.effective_max_active, 1)

    def test_the_container_example_does_not_point_at_the_operators_display(self):
        """Pointing at the host's compositor puts windows on somebody's desktop."""
        run_config = config_module.load(ROOT / "config.distrobox.example.yaml")
        self.assertNotIn(run_config.orca.display, (":0", ""))

    def test_the_artifacts_outlive_the_state_in_every_example(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                run_config = config_module.load(path)
                self.assertFalse(
                    run_config.artifact_root.is_relative_to(run_config.state_root)
                )


class ProvisionIsRealTest(unittest.TestCase):
    """The container backend installs everything per run; an empty list cannot."""

    def test_the_container_example_provisions_what_it_requires(self):
        run_config = config_module.load(ROOT / "config.distrobox.example.yaml")
        steps = " ".join(" ".join(argv) for argv in run_config.distrobox.provision)
        self.assertTrue(run_config.distrobox.provision, "nothing is installed")
        for needed in (
            VERSIONS["tool_image"]["contents"]["orca"]["sha256"],
            VERSIONS["tool_image"]["contents"]["node"]["sha256"],
            VERSIONS["skills"]["plugins"][0]["revision"],
            "skills add",
        ):
            with self.subTest(needed=needed):
                self.assertIn(needed, steps)

    def test_the_machine_example_provisions_nothing_because_its_image_carries_it(self):
        run_config = config_module.load(ROOT / "config.vm.example.yaml")
        self.assertEqual(run_config.vm.provision, ())


if __name__ == "__main__":
    unittest.main()
