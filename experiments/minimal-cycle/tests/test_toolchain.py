"""Which Claude Code the environment runs, as opposed to which one it installed.

Measured on a real box: the provisioning step installed
`@anthropic-ai/claude-code@2.1.272`, symlinked it into /usr/local/bin, and then
ended with a bare `claude --version` that printed 2.1.276 — the operator's own
build, reached through the home Distrobox mounts and the search path the box
inherits. Both numbers were true. Only one of them was about the box.

So the question these answer is not "what was installed" but "what does a bare
name run here", and the answer is compared to the pin rather than printed.
"""

import os
import subprocess
import tempfile
import unittest

from cycle_runner import toolchain
from cycle_runner.status import PhaseStatus

PINNED = "2.1.272"
HOSTS_OWN = "2.1.276"
OWN = "/usr/local/bin/claude"
SHADOW = "/home/someone/.local/bin/claude"


def resolution(selected=OWN, version=PINNED, reachable=(OWN, SHADOW)):
    return {"selected": selected, "selected_version": version, "reachable": reachable}


class ResolutionScriptTest(unittest.TestCase):
    """The probe is a shell script that has to work, not a string to inspect."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.directory, True)

    def plant(self, name, version):
        where = os.path.join(self.directory, name)
        os.makedirs(where, exist_ok=True)
        binary = os.path.join(where, "cycle-fake-tool")
        with open(binary, "w", encoding="utf-8") as handle:
            handle.write(f"#!/bin/sh\nprintf '{version} (a tool)\\n'\n")
        os.chmod(binary, 0o755)
        return where

    def run_probe(self, path):
        completed = subprocess.run(
            toolchain.resolution_argv("cycle-fake-tool"),
            capture_output=True,
            text=True,
            env={"PATH": path},
        )
        return toolchain.parse_resolution(completed.stdout)

    def test_it_names_the_binary_a_bare_name_selects_and_its_version(self):
        first = self.plant("first", PINNED)
        answer = self.run_probe(f"{first}:/usr/bin:/bin")
        self.assertEqual(answer["selected"], os.path.join(first, "cycle-fake-tool"))
        self.assertEqual(answer["selected_version"], PINNED)

    def test_it_names_the_ones_that_lost_as_well_as_the_one_that_won(self):
        """A list of one reads the same whether or not there was a contest."""
        first = self.plant("first", PINNED)
        second = self.plant("second", HOSTS_OWN)
        answer = self.run_probe(f"{first}:{second}:/usr/bin:/bin")
        self.assertEqual(
            list(answer["reachable"]),
            [
                os.path.join(first, "cycle-fake-tool"),
                os.path.join(second, "cycle-fake-tool"),
            ],
        )

    def test_the_order_reported_is_the_order_the_path_would_try(self):
        first = self.plant("first", PINNED)
        second = self.plant("second", HOSTS_OWN)
        answer = self.run_probe(f"{second}:{first}:/usr/bin:/bin")
        self.assertEqual(answer["selected"], os.path.join(second, "cycle-fake-tool"))
        self.assertEqual(
            answer["reachable"][0], os.path.join(second, "cycle-fake-tool")
        )

    def test_a_name_nothing_answers_to_is_reported_as_nothing(self):
        answer = self.run_probe("/usr/bin:/bin")
        self.assertEqual(answer["selected"], "")
        self.assertEqual(answer["reachable"], ())


class ProcessScriptTest(unittest.TestCase):
    """What this run's own processes are executing, and nobody else's."""

    def run_probe(self, home):
        completed = subprocess.run(
            toolchain.process_argv(home), capture_output=True, text=True
        )
        return completed.stdout

    def test_it_finds_this_process_by_the_home_it_was_given(self):
        rows = toolchain.parse_processes(self.run_probe(os.environ.get("HOME", "")))
        self.assertTrue(rows, "the probe found no process of its own")
        self.assertTrue(all(binary for binary, _ in rows))

    def test_a_home_nothing_runs_under_finds_nothing(self):
        """The restriction is what keeps this off the operator's processes."""
        rows = toolchain.parse_processes(
            self.run_probe("/var/empty/no-such-per-run-home")
        )
        self.assertEqual(rows, ())


class ParseTest(unittest.TestCase):
    def test_a_repeated_search_path_entry_is_not_a_second_candidate(self):
        """A real path names ~/.local/bin six times; that is one binary."""
        answer = toolchain.parse_resolution(
            f"selected={SHADOW}\nreachable={SHADOW}\nreachable={SHADOW}\n"
        )
        self.assertEqual(answer["reachable"], (SHADOW,))

    def test_a_process_row_carries_its_executable_and_its_program(self):
        rows = toolchain.parse_processes(f"process={OWN}\t{OWN} --help\nexamined=1\n")
        self.assertEqual(rows, ((OWN, f"{OWN} --help"),))

    def test_an_interpreter_running_the_program_is_still_the_program(self):
        """A shebang names node in /proc; the program is on the command line."""
        rows = toolchain.parse_processes(
            "process=/usr/local/node/bin/node\t/usr/local/bin/claude\n"
        )
        self.assertEqual(
            toolchain.running(rows, "claude"), ("/usr/local/node/bin/node",)
        )


class JudgeTest(unittest.TestCase):
    def judge(self, **overrides):
        arguments = {
            "program": "claude",
            "pinned": PINNED,
            "resolution": resolution(),
            "agent_binaries": (OWN,),
        }
        arguments.update(overrides)
        return toolchain.judge(**arguments)

    def test_the_pinned_build_winning_is_the_only_pass(self):
        record = self.judge()
        self.assertIs(record.status, PhaseStatus.OK)
        self.assertTrue(record.established)

    def test_the_hosts_build_winning_blocks_the_run(self):
        record = self.judge(
            resolution=resolution(selected=SHADOW, version=HOSTS_OWN)
        )
        self.assertIs(record.status, PhaseStatus.BLOCKED)
        self.assertIn(HOSTS_OWN, record.detail)
        self.assertIn(PINNED, record.detail)

    def test_a_version_that_is_merely_printed_is_not_a_pass(self):
        """The defect this exists for: a number nobody compared."""
        record = self.judge(resolution=resolution(version=""))
        self.assertIs(record.status, PhaseStatus.BLOCKED)

    def test_nothing_answering_to_the_name_blocks_the_run(self):
        record = self.judge(resolution=resolution(selected="", version=""))
        self.assertIs(record.status, PhaseStatus.BLOCKED)

    def test_the_shadow_that_lost_is_recorded_beside_the_one_that_won(self):
        """What it would have resolved to, without needing a second run."""
        record = self.judge()
        self.assertEqual(record.shadowed, (SHADOW,))
        self.assertEqual(record.selected, OWN)

    def test_an_environment_with_no_shadow_at_all_still_passes(self):
        record = self.judge(resolution=resolution(reachable=(OWN,)))
        self.assertIs(record.status, PhaseStatus.OK)
        self.assertEqual(record.shadowed, ())

    def test_a_process_that_had_already_gone_leaves_it_unestablished(self):
        """Ordering is right; what the agent ran is a separate claim."""
        record = self.judge(agent_binaries=())
        self.assertIs(record.status, PhaseStatus.OK)
        self.assertFalse(record.established)
        self.assertIn("not established", record.detail)

    def test_what_the_agent_ran_appears_in_the_record(self):
        record = self.judge(agent_binaries=("/usr/local/node/bin/node",))
        self.assertEqual(record.agent_binaries, ("/usr/local/node/bin/node",))
        self.assertIn("/usr/local/node/bin/node", record.detail)


if __name__ == "__main__":
    unittest.main()
