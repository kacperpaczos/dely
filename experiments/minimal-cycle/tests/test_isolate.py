"""The host's own session must not reach into the environment.

Observed on a real container: this runner is itself launched from an Orca
terminal, which exports ORCA_TERMINAL_HANDLE and ORCA_AGENT_HOOK_TOKEN among
others. Distrobox inherits the host environment, so the container's Orca
attested as the *host's* terminal and refused the run with
`consumer_fenced: This terminal is attested as … and cannot act as …`.

That is an identifier of the host leaking into the environment, and one of the
leaked values is a token.
"""

import shutil
import unittest

from cycle_runner import isolate, proc


class ScrubbedArgvTest(unittest.TestCase):
    def test_the_original_command_still_runs(self):
        outcome = proc.run(
            isolate.without_host_session(["printf", "%s", "ran"]),
            timeout=30, context="host",
        )
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.stdout, "ran")

    def test_a_leaking_variable_does_not_reach_the_command(self):
        outcome = proc.run(
            isolate.without_host_session(["sh", "-c", 'echo "[${ORCA_TERMINAL_HANDLE:-gone}]"']),
            timeout=30, context="host",
            env={"ORCA_TERMINAL_HANDLE": "term_from_the_host"},
        )
        self.assertIn("[gone]", outcome.stdout)
        self.assertNotIn("term_from_the_host", outcome.stdout)

    def test_every_leaking_variable_goes_not_just_the_one_we_named(self):
        outcome = proc.run(
            isolate.without_host_session(["sh", "-c", "env | grep -c '^ORCA_' || true"]),
            timeout=30, context="host",
            env={
                "ORCA_TERMINAL_HANDLE": "a",
                "ORCA_AGENT_HOOK_TOKEN": "b",
                "ORCA_PANE_KEY": "c",
            },
        )
        self.assertEqual(outcome.stdout.strip(), "0")

    def test_what_the_run_deliberately_passes_survives(self):
        outcome = proc.run(
            isolate.without_host_session(["sh", "-c", 'echo "[${CLAUDE_CODE_OAUTH_TOKEN:-gone}]"']),
            timeout=30, context="host",
            env={"CLAUDE_CODE_OAUTH_TOKEN": "kept", "ORCA_TERMINAL_HANDLE": "a"},
        )
        self.assertIn("[kept]", outcome.stdout)

    def test_arguments_with_spaces_and_quotes_survive(self):
        outcome = proc.run(
            isolate.without_host_session(["printf", "%s|%s", "two words", "it's"]),
            timeout=30, context="host",
        )
        self.assertEqual(outcome.stdout, "two words|it's")

    def test_the_prefixes_are_named_and_include_the_session_handle(self):
        self.assertIn("ORCA_", isolate.LEAKING_PREFIXES)


class OwnToolchainFirstTest(unittest.TestCase):
    """The environment's own installation has to win a bare program name.

    Observed on a real box: it unpacked the pinned node into /usr/local and
    then printed the operator's node, because Distrobox mounts the host home
    and the inherited search path opens with directories inside it. The same
    ordering decided which Claude Code the execution plane launched, which is
    not a cosmetic difference.

    The decoy below shadows a real system program, so this fails for the same
    reason the box did rather than by inspecting a string.
    """

    def decoy(self, name: str) -> str:
        import os
        import tempfile

        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        planted = os.path.join(directory, name)
        with open(planted, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nprintf the-decoy\n")
        os.chmod(planted, 0o755)
        return directory

    def test_a_shadowing_binary_earlier_on_the_path_does_not_win(self):
        directory = self.decoy("uname")
        outcome = proc.run(
            isolate.without_host_session(["sh", "-c", "uname"]),
            timeout=30,
            context="host",
            env={"PATH": f"{directory}:/usr/bin:/bin"},
        )
        self.assertEqual(outcome.exit_code, 0)
        self.assertNotIn("the-decoy", outcome.stdout)

    def test_the_shadowed_directory_is_still_reachable(self):
        """Prepending, not replacing: the mounted home carries the credential."""
        directory = self.decoy("uname")
        outcome = proc.run(
            isolate.without_host_session(["sh", "-c", 'printf %s "$PATH"']),
            timeout=30,
            context="host",
            env={"PATH": f"{directory}:/usr/bin"},
        )
        self.assertTrue(outcome.stdout.startswith("/usr/local/sbin:/usr/local/bin:"))
        self.assertIn(directory, outcome.stdout)

    def test_an_empty_inherited_path_gains_no_trailing_separator(self):
        """A trailing separator is the working directory, on the search path."""
        self.assertEqual(
            isolate.own_toolchain_first(""), ":".join(isolate.OWN_DIRECTORIES)
        )
        self.assertFalse(isolate.own_toolchain_first("").endswith(":"))

    def test_the_helper_and_the_script_agree_on_the_order(self):
        """The name a reader checks and the script a run uses are one rule.

        The inherited path keeps a directory holding a shell, because this
        launches one; what is being compared is the order, not the contents.
        """
        inherited = "/bin:/opt/somewhere"
        outcome = proc.run(
            isolate.without_host_session(["sh", "-c", 'printf %s "$PATH"']),
            timeout=30,
            context="host",
            env={"PATH": inherited},
        )
        self.assertEqual(outcome.stdout, isolate.own_toolchain_first(inherited))


class AdaptersScrubTest(unittest.TestCase):
    """Both backends run their commands with the host's session removed."""

    def test_the_container_backend_scrubs(self):
        import tempfile
        from pathlib import Path

        from cycle_runner import config as config_module
        from cycle_runner.adapters import distrobox
        from tests.test_adapter_distrobox import StubRunner
        from tests.test_config import minimal_document

        with tempfile.TemporaryDirectory() as root:
            document = minimal_document()
            document["state_root"] = str(Path(root) / "state")
            document["artifact_root"] = str(Path(root) / "artifacts")
            runner = StubRunner()
            adapter = distrobox.DistroboxAdapter(
                run_config=config_module.from_document(document),
                run_id="20260914T221530Z-abc123-0123abcd",
                host_home=Path(root) / "hosthome",
                runner=runner,
                which=lambda name: f"/usr/bin/{name}",
            )
            adapter.execute(["true"], timeout=5)
            self.assertIn("cycle-isolate", runner.seen[-1])

    def test_the_machine_backend_scrubs(self):
        import tempfile
        from pathlib import Path

        from tests.test_adapter_vm import VmTestCase

        case = VmTestCase("run")
        case.setUp()
        try:
            adapter = case.make()
            adapter.prepare_identity()
            adapter.address = "192.168.122.11"
            adapter.execute(["true"], timeout=5)
            # The transport sends one quoted string, so the marker is inside it.
            self.assertIn("cycle-isolate", " ".join(adapter.runner.seen[-1]))
        finally:
            case.doCleanups()


class ScrubbedEnvironmentTest(unittest.TestCase):
    """The command that creates an environment needs scrubbing too."""

    BASE = {
        "PATH": "/usr/bin",
        "HOME": "/home/somebody",
        "LANG": "en_GB.UTF-8",
        "WAYLAND_DISPLAY": "wayland-0",
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "ORCA_PANE_KEY": "a-pane",
        "ORCA_AGENT_HOOK_TOKEN": "a-token",
    }

    def test_the_operators_session_is_removed(self):
        scrubbed = isolate.scrubbed(self.BASE)
        for name in (
            "WAYLAND_DISPLAY",
            "DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "ORCA_PANE_KEY",
            "ORCA_AGENT_HOOK_TOKEN",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, scrubbed)

    def test_everything_the_command_still_needs_is_kept(self):
        scrubbed = isolate.scrubbed(self.BASE)
        self.assertEqual(scrubbed["PATH"], "/usr/bin")
        self.assertEqual(scrubbed["HOME"], "/home/somebody")
        self.assertEqual(scrubbed["LANG"], "en_GB.UTF-8")

    def test_a_prefix_covers_a_variable_nobody_listed(self):
        self.assertNotIn("ORCA_SOMETHING_NEW", isolate.scrubbed({"ORCA_SOMETHING_NEW": "x"}))

    def test_an_empty_environment_scrubs_to_an_empty_one(self):
        self.assertEqual(isolate.scrubbed({}), {})
