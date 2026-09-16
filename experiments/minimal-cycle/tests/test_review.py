"""Two panes are not two agents, and a verdict is not a review of anything."""

import unittest

from cycle_runner import review
from cycle_runner.result import WorkerRecord

DIGEST = "1702d4bb1449b8295118f8d6c14fb85ad05262f47bf30f5efff22237f98dd88a"
OTHER = "0" * 64


def agent(dispatch="dispatch-1", terminal="terminal-1", run="run-1"):
    return WorkerRecord(dispatch_id=dispatch, terminal=terminal, run_id=run)


class SeparationTest(unittest.TestCase):
    def test_two_dispatches_under_one_run_are_separate(self):
        verdict = review.separation(
            agent("dispatch-1", "terminal-1"), agent("dispatch-2", "terminal-2")
        )
        self.assertTrue(verdict.ok, verdict.detail)
        self.assertIn("two dispatches", verdict.detail)

    def test_one_dispatch_answering_twice_is_not_two_agents(self):
        verdict = review.separation(
            agent("dispatch-1", "terminal-1"), agent("dispatch-1", "terminal-2")
        )
        self.assertFalse(verdict.ok)
        self.assertIn("same identifier", verdict.detail)

    def test_two_dispatches_sharing_a_terminal_are_not_separate_sessions(self):
        verdict = review.separation(
            agent("dispatch-1", "terminal-1"), agent("dispatch-2", "terminal-1")
        )
        self.assertFalse(verdict.ok)
        self.assertIn("same terminal", verdict.detail)

    def test_a_review_outside_the_run_is_not_a_handoff(self):
        verdict = review.separation(
            agent(run="run-1"), agent("dispatch-2", "terminal-2", run="run-2")
        )
        self.assertFalse(verdict.ok)
        self.assertIn("does not belong", verdict.detail)

    def test_an_unnamed_dispatch_proves_nothing(self):
        verdict = review.separation(agent(dispatch=None), agent("dispatch-2", "terminal-2"))
        self.assertFalse(verdict.ok)
        self.assertIn("never named", verdict.detail)

    def test_an_unnamed_terminal_proves_nothing(self):
        verdict = review.separation(agent(), agent("dispatch-2", terminal=None))
        self.assertFalse(verdict.ok)
        self.assertIn("never named", verdict.detail)


class SameDiffTest(unittest.TestCase):
    def test_the_same_bytes_throughout_holds(self):
        ok, why = review.judge_same_diff(captured=DIGEST, after=DIGEST, reported=DIGEST)
        self.assertTrue(ok, why)

    def test_a_diff_that_changed_under_the_review_does_not(self):
        ok, why = review.judge_same_diff(captured=DIGEST, after=OTHER, reported=DIGEST)
        self.assertFalse(ok)
        self.assertIn("changed while the review was running", why)

    def test_a_diff_that_vanished_does_not(self):
        ok, why = review.judge_same_diff(captured=DIGEST, after="missing", reported=DIGEST)
        self.assertFalse(ok)
        self.assertIn("gone by the time", why)

    def test_a_reviewer_that_reported_another_diff_does_not(self):
        ok, why = review.judge_same_diff(captured=DIGEST, after=DIGEST, reported=OTHER)
        self.assertFalse(ok)
        self.assertIn("a different diff", why)

    def test_a_reviewer_that_reported_nothing_does_not(self):
        ok, why = review.judge_same_diff(captured=DIGEST, after=DIGEST, reported="")
        self.assertFalse(ok)
        self.assertIn("did not report", why)

    def test_nothing_captured_means_nothing_was_reviewed(self):
        ok, why = review.judge_same_diff(captured="", after="", reported="")
        self.assertFalse(ok)
        self.assertIn("nothing a review was of", why)


class ParsingTest(unittest.TestCase):
    def test_the_capture_output_is_read(self):
        measured = review.parse_capture(f"sha256 {DIGEST}\nlines 8\nbytes 240\n")
        self.assertEqual(measured, {"sha256": DIGEST, "lines": 8, "bytes": 240})

    def test_a_verdict_is_read_from_its_own_line(self):
        verdict = review.parse_verdict(
            'noise before\n{"verdict": "accept", "diff_sha256": "' + DIGEST + '"}\n'
        )
        self.assertEqual(verdict["verdict"], "accept")

    def test_an_absent_verdict_is_an_empty_one(self):
        self.assertEqual(review.parse_verdict("{}\n"), {})
        self.assertEqual(review.parse_verdict(""), {})

    def test_the_prompt_names_the_diff_and_the_verdict_it_wants(self):
        prompt = review.build_prompt(
            diff_path="/h/handoff-diff.patch",
            project_path="/h/project",
            verdict_path="/h/review-verdict.json",
            relative_path="evidence.txt",
            marker="dely-cycle-marker",
        )
        for needed in (
            "/h/handoff-diff.patch",
            "/h/review-verdict.json",
            "evidence.txt",
            "dely-cycle-marker",
            "diff_sha256",
        ):
            self.assertIn(needed, prompt)

    def test_the_capture_stages_new_files(self):
        """`git diff` alone omits an untracked file, which is what the task makes."""
        self.assertIn("add -A", review.CAPTURE_SCRIPT)
        self.assertIn("diff --cached", review.CAPTURE_SCRIPT)


if __name__ == "__main__":
    unittest.main()
