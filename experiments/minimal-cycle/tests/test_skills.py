"""A skill the environment names is not a skill the environment has."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from cycle_runner import skills

ORCHESTRATION = "f7da0dd40d8681e2b0303fa0fa2f7ee4e36f2eca6495a4af57b92b9857dff732"
ORCA_CLI = "b21b9b80475c35996b9c046379b9c9fa788f2d357c9e917befd8ab1b37df2e77"
COMMIT = "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
OTHER_COMMIT = "0000000000000000000000000000000000000000"


def judge(bundled=(), plugins=(), skill_answers=None, plugin_answers=None):
    return skills.judge(
        bundled=bundled,
        plugins=plugins,
        skill_answers=skill_answers or {},
        plugin_answers=plugin_answers or {},
    )


class JudgementTest(unittest.TestCase):
    def test_a_skill_at_the_pinned_digest_holds(self):
        findings, ok = judge(
            bundled=[("orchestration", ORCHESTRATION)],
            skill_answers={"orchestration": ("/home/a/.agents/skills/orchestration/SKILL.md", ORCHESTRATION)},
        )
        self.assertTrue(ok)
        self.assertEqual(findings[0].where, "/home/a/.agents/skills/orchestration/SKILL.md")

    def test_a_skill_that_is_not_anywhere_the_agent_looks_fails(self):
        findings, ok = judge(
            bundled=[("orchestration", ORCHESTRATION)],
            skill_answers={"orchestration": ("missing", "")},
        )
        self.assertFalse(ok)
        self.assertIn("no SKILL.md", findings[0].detail)

    def test_a_skill_with_a_different_digest_fails(self):
        findings, ok = judge(
            bundled=[("orchestration", ORCHESTRATION)],
            skill_answers={"orchestration": ("/somewhere/SKILL.md", ORCA_CLI)},
        )
        self.assertFalse(ok)
        self.assertIn("fetched from somewhere else", findings[0].detail)

    def test_an_unanswered_skill_fails_rather_than_passing(self):
        findings, ok = judge(bundled=[("orchestration", ORCHESTRATION)])
        self.assertFalse(ok)
        self.assertIn("did not answer", findings[0].detail)

    def test_a_plugin_at_the_pinned_commit_holds(self):
        _, ok = judge(
            plugins=[("superpowers", COMMIT)],
            plugin_answers={"superpowers": (COMMIT, "/opt/skills/superpowers")},
        )
        self.assertTrue(ok)

    def test_a_plugin_at_another_commit_fails(self):
        findings, ok = judge(
            plugins=[("superpowers", COMMIT)],
            plugin_answers={"superpowers": (OTHER_COMMIT, "/opt/skills/superpowers")},
        )
        self.assertFalse(ok)
        self.assertIn("different commit", findings[0].detail)

    def test_a_plugin_with_no_checkout_fails(self):
        findings, ok = judge(
            plugins=[("superpowers", COMMIT)],
            plugin_answers={"superpowers": ("missing", "/opt/skills/superpowers")},
        )
        self.assertFalse(ok)
        self.assertIn("no checkout", findings[0].detail)


class RecordTest(unittest.TestCase):
    def test_a_required_skill_that_is_absent_blocks(self):
        findings, _ = judge(
            bundled=[("orchestration", ORCHESTRATION)],
            skill_answers={"orchestration": ("missing", "")},
        )
        record = skills.record(findings, required=True)
        self.assertEqual(record.status.value, "BLOCKED")
        self.assertIn("does not carry what was pinned", record.detail)

    def test_everything_pinned_and_present_is_ok(self):
        findings, _ = judge(
            bundled=[("orchestration", ORCHESTRATION)],
            skill_answers={"orchestration": ("/p/SKILL.md", ORCHESTRATION)},
        )
        self.assertEqual(skills.record(findings, required=True).status.value, "OK")

    def test_requiring_nothing_is_recorded_as_requiring_nothing(self):
        record = skills.record([], required=True)
        self.assertEqual(record.status.value, "SKIPPED")
        self.assertIn("requires no skills", record.detail)


class ProbeTest(unittest.TestCase):
    """The probe is a shell script; run it and see what it says."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def install(self, root, name, body):
        directory = self.home / root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(body, encoding="utf-8")

    def probe(self, roots, names):
        completed = subprocess.run(
            skills.locate_argv(roots, names),
            capture_output=True,
            text=True,
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return skills.parse(completed.stdout)

    def test_it_finds_a_skill_and_reports_its_digest(self):
        self.install(".agents/skills", "orchestration", "hello\n")
        answers = self.probe([".agents/skills", ".claude/skills"], ["orchestration"])
        where, digest = answers["orchestration"]
        self.assertTrue(where.endswith(".agents/skills/orchestration/SKILL.md"))
        self.assertEqual(len(digest), 64)

    def test_it_searches_every_root_in_order(self):
        self.install(".claude/skills", "orca-cli", "second root\n")
        answers = self.probe([".agents/skills", ".claude/skills"], ["orca-cli"])
        self.assertIn(".claude/skills", answers["orca-cli"][0])

    def test_it_says_missing_rather_than_nothing(self):
        answers = self.probe([".agents/skills"], ["absent"])
        self.assertEqual(answers["absent"], ("missing", ""))

    def test_the_digest_changes_when_the_file_does(self):
        self.install(".agents/skills", "one", "a\n")
        first = self.probe([".agents/skills"], ["one"])["one"][1]
        (self.home / ".agents/skills/one/SKILL.md").write_text("b\n", encoding="utf-8")
        second = self.probe([".agents/skills"], ["one"])["one"][1]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
