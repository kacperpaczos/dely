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
            VERSIONS["skills"]["bundled"]["revision"],
        ):
            with self.subTest(needed=needed):
                self.assertIn(needed, steps)

    def test_the_container_example_installs_what_a_screen_capture_needs(self):
        """No hypervisor holds this box's screen, so its X server has to be asked."""
        run_config = config_module.load(ROOT / "config.distrobox.example.yaml")
        steps = " ".join(" ".join(argv) for argv in run_config.distrobox.provision)
        for needed in ("x11-apps", "netpbm"):
            with self.subTest(needed=needed):
                self.assertIn(needed, steps)

    def test_the_machine_example_provisions_nothing_because_its_image_carries_it(self):
        run_config = config_module.load(ROOT / "config.vm.example.yaml")
        self.assertEqual(run_config.vm.provision, ())


class ConfirmationsCompareTest(unittest.TestCase):
    """A step that prints a version has confirmed nothing.

    Measured on a real run: the step that unpacked node v24.21.0 ended with
    `node --version` and printed v24.19.0, and the step that installed
    claude-code 2.1.272 ended with `claude --version` and printed 2.1.276.
    Both numbers were the operator's own build, reached through the home
    Distrobox mounts and the search path the box inherits. Neither step
    failed, because neither step compared anything.

    So a confirmation has to read the binary it just installed by its absolute
    path, compare what it reports to the pin, and refuse on a mismatch.
    """

    #: Where each pinned tool lands, and the pin it has to agree with.
    PINNED = {
        "node": (
            "/usr/local/bin/node",
            VERSIONS["tool_image"]["contents"]["node"]["version"],
        ),
        "claude": (
            "/usr/local/bin/claude",
            VERSIONS["tool_image"]["contents"]["claude_code"]["version"],
        ),
    }

    IMAGE_PROVISION = (ROOT / "host" / "packer" / "provision.sh").read_text(
        encoding="utf-8"
    )

    def installing_step(self, program: str) -> str:
        """Return the container step that puts this program in /usr/local/bin."""
        run_config = config_module.load(ROOT / "config.distrobox.example.yaml")
        where = self.PINNED[program][0]
        found = [
            " ".join(argv)
            for argv in run_config.distrobox.provision
            if f"ln -sf" in " ".join(argv) and where in " ".join(argv)
        ]
        self.assertEqual(len(found), 1, f"{program} is installed by {len(found)} steps")
        return found[0]

    def test_each_pinned_tool_is_read_by_its_absolute_path(self):
        for program, (where, _) in self.PINNED.items():
            with self.subTest(program=program):
                self.assertIn(f"{where} --version", self.installing_step(program))

    def test_no_step_confirms_a_pinned_tool_by_bare_name(self):
        """The bare name reads the search path, which is what went wrong."""
        for program in self.PINNED:
            with self.subTest(program=program):
                step = self.installing_step(program)
                self.assertNotIn(f"; {program} --version", step)

    def test_each_pinned_tool_is_compared_to_the_pin(self):
        for program, (_, pinned) in self.PINNED.items():
            with self.subTest(program=program):
                self.assertIn(f"!= {pinned}", self.installing_step(program))

    def test_a_mismatch_fails_the_step_rather_than_printing(self):
        for program in self.PINNED:
            with self.subTest(program=program):
                self.assertIn("exit 5", self.installing_step(program))

    def test_the_image_build_compares_too(self):
        """No host home is mounted there, but a confirmation still confirms."""
        for program, (where, _) in self.PINNED.items():
            with self.subTest(program=program):
                self.assertIn(f"{where} --version", self.IMAGE_PROVISION)
                self.assertNotIn(f"\n{program} --version\n", self.IMAGE_PROVISION)

    def test_the_container_example_gives_the_box_an_orca_of_its_own(self):
        """Otherwise the name resolves through the operator's mounted home."""
        run_config = config_module.load(ROOT / "config.distrobox.example.yaml")
        steps = " ".join(" ".join(argv) for argv in run_config.distrobox.provision)
        self.assertIn("/usr/local/bin/orca", steps)

    def test_the_machine_example_confirms_nothing_because_it_installs_nothing(self):
        """Its toolchain is in the image; the config has no step to fix."""
        run_config = config_module.load(ROOT / "config.vm.example.yaml")
        self.assertEqual(run_config.vm.provision, ())


class SkillsComeFromAPinnedRevisionTest(unittest.TestCase):
    """An install that names no revision installs the tip, which is not a pin.

    The digests in `skills.bundled` say what Orca 1.4.201 ships. `npx skills add
    <repo>` — which is what `orca skills install` resolves to — names no
    revision and has no flag that takes one, so it fetches the repository's tip
    and agrees with those digests only on the days the tip happens to equal what
    the pinned release shipped. The container backend blocked the day it stopped
    being one of those days; the image backend would have baked the wrong bytes
    in silently, because a built image freezes whatever the tip was that day.

    So both places name a commit, and these are the tests that keep them naming
    one. The runner's digest check stays where it is: it is what catches a pin
    that is right on paper and wrong in the environment.
    """

    IMAGE_PROVISION = (ROOT / "host" / "packer" / "provision.sh").read_text(
        encoding="utf-8"
    )
    TEMPLATE = (ROOT / "host" / "packer" / "tool-image.pkr.hcl").read_text(
        encoding="utf-8"
    )
    BUILD = (ROOT / "host" / "packer" / "build-tool-image").read_text(encoding="utf-8")

    def container_steps(self) -> str:
        run_config = config_module.load(ROOT / "config.distrobox.example.yaml")
        return " ".join(" ".join(argv) for argv in run_config.distrobox.provision)

    def test_the_host_records_a_repository_and_a_commit_for_the_bundled_skills(self):
        bundled = VERSIONS["skills"]["bundled"]
        self.assertTrue(bundled.get("repository"))
        self.assertRegex(bundled.get("revision", ""), r"^[0-9a-f]{40}$")

    def test_the_container_example_takes_them_from_the_pinned_commit(self):
        steps = self.container_steps()
        self.assertIn(VERSIONS["skills"]["bundled"]["revision"], steps)

    def test_the_container_example_names_no_unrevisioned_install(self):
        self.assertNotIn("skills add", self.container_steps())

    def test_the_image_takes_them_from_the_pinned_commit(self):
        self.assertIn("ORCA_SKILLS_REVISION", self.IMAGE_PROVISION)
        self.assertIn("orca_skills_revision", self.TEMPLATE)
        self.assertIn("orca_skills_revision", self.BUILD)

    def test_the_image_names_no_unrevisioned_install(self):
        """Comments are allowed to name it; only what the build runs is checked."""
        commands = "\n".join(
            line
            for line in self.IMAGE_PROVISION.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("skills add", commands)

    def test_the_image_still_checks_the_installed_bytes_against_the_pin(self):
        """The pin says what should land; the digest says what did."""
        self.assertIn("SKILL_DIGESTS", self.IMAGE_PROVISION)
        self.assertIn("not the pinned", self.IMAGE_PROVISION)


if __name__ == "__main__":
    unittest.main()
