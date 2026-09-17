"""A settled Task is not a released terminal, and a receipt is not a closure."""

import json
import unittest

from cycle_runner import status, terminals


def worker_list(*rows):
    return json.dumps(
        {
            "id": "request-fake",
            "ok": True,
            "result": {
                "workers": list(rows),
                "page": {"limit": 100, "total": len(rows), "hasMore": False},
                "scope": {"source": "flag"},
            },
        }
    )


def row(dispatch, terminal, state="reclaimable"):
    return {
        "dispatchId": dispatch,
        "taskId": "task-one",
        "runId": "run-one",
        "agentTerminalHandle": terminal,
        "terminalState": state,
    }


def terminal_list(*handles):
    return json.dumps(
        {
            "ok": True,
            "result": {
                "terminals": [{"handle": handle} for handle in handles],
                "totalCount": len(handles),
            },
        }
    )


class ArgvTest(unittest.TestCase):
    """The flags are the plane's, not this runner's idea of them."""

    def test_the_owed_list_is_scoped_to_this_run_and_to_one_state(self):
        argv = terminals.owed_argv("run-one")
        self.assertEqual(argv[:3], ["orca", "orchestration", "worker-list"])
        self.assertEqual(argv[3:5], ["--run", "run-one"])
        self.assertEqual(argv[5:7], ["--terminal-state", "reclaimable"])
        self.assertIn("--json", argv)

    def test_a_release_names_a_dispatch_and_never_a_terminal_handle(self):
        """The verb closes the terminal that dispatch owns and refuses others."""
        argv = terminals.release_argv("ctx-one")
        self.assertEqual(argv[:3], ["orca", "orchestration", "worker-release"])
        self.assertEqual(argv[3:5], ["--dispatch", "ctx-one"])
        self.assertNotIn("--terminal", argv)

    def test_the_unfiltered_list_carries_every_state(self):
        self.assertNotIn("--terminal-state", terminals.workers_argv("run-one"))

    def test_the_live_list_is_the_terminal_verb_not_the_accounting_one(self):
        self.assertEqual(
            terminals.live_argv(), ["orca", "terminal", "list", "--json"]
        )


class ParseTest(unittest.TestCase):
    def test_a_worker_row_carries_the_dispatch_its_terminal_and_its_state(self):
        rows = terminals.parse_workers(worker_list(row("ctx-one", "term-one")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].dispatch_id, "ctx-one")
        self.assertEqual(rows[0].terminal, "term-one")
        self.assertEqual(rows[0].terminal_state, "reclaimable")
        self.assertEqual(rows[0].run_id, "run-one")

    def test_a_row_with_no_dispatch_is_dropped_because_nothing_can_be_done_with_it(self):
        rows = terminals.parse_workers(
            worker_list(row("", "term-one"), row("ctx-two", "term-two"))
        )
        self.assertEqual([entry.dispatch_id for entry in rows], ["ctx-two"])

    def test_a_reply_that_is_not_a_document_names_nothing(self):
        self.assertEqual(terminals.parse_workers("command not found"), [])
        self.assertEqual(terminals.parse_live(""), [])

    def test_the_rows_come_from_the_result_and_not_from_the_envelope(self):
        """The envelope's own fields belong to the request, not to the answer."""
        envelope = json.dumps({"id": "request-fake", "workers": [row("ctx", "term")]})
        self.assertEqual(terminals.parse_workers(envelope), [])

    def test_the_live_list_is_the_handles_of_the_terminals_that_exist(self):
        self.assertEqual(
            terminals.parse_live(terminal_list("term-one", "term-two")),
            ["term-one", "term-two"],
        )

    def test_the_outcome_is_read_from_the_release_reply(self):
        reply = json.dumps({"ok": True, "result": {"outcome": "already_released"}})
        self.assertEqual(terminals.release_outcome(reply), "already_released")

    def test_a_reply_with_no_outcome_reports_none_rather_than_inventing_one(self):
        self.assertEqual(terminals.release_outcome('{"ok": true, "result": {}}'), "")

    def test_the_outcome_is_read_from_the_field_the_plane_really_sends(self):
        """The receipt names the answer `state`, beside the dispatch it is about.

        This is the shape a real `worker-release` returned, not a shape this
        test invented: the plane's own receipt schema is dispatchId, state,
        processAction, archive, and the lookup has to find that one.
        """
        reply = json.dumps(
            {
                "ok": True,
                "result": {
                    "dispatchId": "ctx-one",
                    "state": "released",
                    "processAction": "closed_agent_terminal",
                },
            }
        )
        self.assertEqual(terminals.release_outcome(reply), "released")


class ReleaseTest(unittest.TestCase):
    """Only the answers the plane calls a discharge are one."""

    def judge(self, outcome, **overrides):
        options = {
            "role": "implementer",
            "row": terminals.WorkerRow(dispatch_id="ctx-one", terminal="term-one"),
            "outcome": outcome,
        }
        options.update(overrides)
        return terminals.judge_release(**options)

    def test_a_pending_release_discharges_the_decision(self):
        disposition = self.judge("release_pending")
        self.assertTrue(disposition.ok, disposition.detail)
        self.assertEqual(disposition.action, "release")
        self.assertEqual(disposition.terminal, "term-one")

    def test_a_closed_terminal_is_the_plainest_discharge_there_is(self):
        """`released` means the terminal is gone, which is the whole point.

        It was missing from the discharging answers, so the one reply that says
        the release did exactly what it was asked to do was judged a failure.
        """
        disposition = self.judge("released")
        self.assertTrue(disposition.ok, disposition.detail)
        self.assertIn("released", disposition.detail)

    def test_repeating_a_release_is_still_a_release(self):
        self.assertTrue(self.judge("already_released").ok)

    def test_a_dispatch_that_owned_no_terminal_is_discharged_too(self):
        self.assertTrue(self.judge("retained").ok)

    def test_an_answer_the_plane_gives_to_say_it_does_not_know_is_not_a_release(self):
        disposition = self.judge("release_unknown")
        self.assertFalse(disposition.ok)
        self.assertIn("could not say", disposition.detail)

    def test_an_answer_nobody_recognises_is_not_a_release_either(self):
        disposition = self.judge("something_new")
        self.assertFalse(disposition.ok)
        self.assertIn("something_new", disposition.detail)

    def test_no_answer_at_all_is_not_a_release(self):
        disposition = self.judge("")
        self.assertFalse(disposition.ok)

    def test_a_command_that_never_ran_is_recorded_rather_than_raised(self):
        disposition = self.judge("", failure="the transport refused the command")
        self.assertFalse(disposition.ok)
        self.assertEqual(disposition.detail, "the transport refused the command")


class DebtTest(unittest.TestCase):
    """The turn is not over while the plane still names a terminal."""

    def test_an_empty_answer_is_the_only_one_that_ends_the_turn(self):
        clean, why = terminals.judge_debt([])
        self.assertTrue(clean)
        self.assertIn("owing nothing", why)

    def test_a_terminal_still_owing_a_decision_is_named_with_its_dispatch(self):
        clean, why = terminals.judge_debt(
            [terminals.WorkerRow(dispatch_id="ctx-one", terminal="term-one")]
        )
        self.assertFalse(clean)
        self.assertIn("term-one", why)
        self.assertIn("ctx-one", why)

    def test_every_owed_terminal_is_named_rather_than_counted(self):
        clean, why = terminals.judge_debt(
            [
                terminals.WorkerRow(dispatch_id="ctx-one", terminal="term-one"),
                terminals.WorkerRow(dispatch_id="ctx-two", terminal="term-two"),
            ]
        )
        self.assertFalse(clean)
        self.assertIn("term-one", why)
        self.assertIn("term-two", why)


class DistinguishesTest(unittest.TestCase):
    """A released terminal leaves the live list; a hidden panel does not."""

    def distinguishes(self, **overrides):
        options = {
            "before": ["term-coordinator", "term-one"],
            "after": ["term-coordinator"],
            "released": ["term-one"],
        }
        options.update(overrides)
        return terminals.distinguishes(**options)

    def test_a_terminal_that_left_the_live_list_was_closed(self):
        ok, how = self.distinguishes()
        self.assertTrue(ok, how)
        self.assertIn("closure rather than a disappearance", how)

    def test_a_terminal_still_in_the_live_list_was_not_closed(self):
        """Whatever the receipt said, and whatever its panel did."""
        ok, how = self.distinguishes(after=["term-coordinator", "term-one"])
        self.assertFalse(ok)
        self.assertIn("still in the live list", how)
        self.assertIn("term-one", how)

    def test_a_terminal_that_was_never_in_the_live_list_establishes_nothing(self):
        ok, how = self.distinguishes(before=["term-coordinator"])
        self.assertFalse(ok)
        self.assertIn("establishes nothing", how)

    def test_releasing_nothing_tells_nothing_apart(self):
        ok, how = self.distinguishes(released=[])
        self.assertFalse(ok)
        self.assertIn("no terminal was released", how)


class RecordTest(unittest.TestCase):
    def snapshot(self, live, states):
        return terminals.Snapshot(
            live=tuple(live),
            workers=tuple(
                terminals.WorkerRow(
                    dispatch_id=dispatch, terminal=terminal, terminal_state=state
                )
                for dispatch, terminal, state in states
            ),
        )

    def settled(self, **overrides):
        owed = [
            terminals.WorkerRow(
                dispatch_id="ctx-one",
                terminal="term-one",
                terminal_state="reclaimable",
            )
        ]
        options = {
            "run_id": "run-one",
            "before": self.snapshot(
                ["term-coordinator", "term-one"],
                [("ctx-one", "term-one", "reclaimable")],
            ),
            "after": self.snapshot(
                ["term-coordinator"], [("ctx-one", "term-one", "released")]
            ),
            "owed_before": owed,
            "owed_after": [],
            "dispositions": [
                terminals.judge_release(
                    role="implementer", row=owed[0], outcome="release_pending"
                )
            ],
        }
        options.update(overrides)
        return terminals.record(**options)

    def test_a_turn_that_owes_nothing_is_recorded_as_owing_nothing(self):
        record = self.settled()
        self.assertEqual(record.status, status.PhaseStatus.OK)
        self.assertTrue(record.clean)
        self.assertTrue(record.distinguished, record.distinction_detail)
        self.assertEqual(record.run_id, "run-one")

    def test_the_state_either_side_of_the_release_is_kept(self):
        document = self.settled().to_document()
        self.assertEqual(document["live_before"], ["term-coordinator", "term-one"])
        self.assertEqual(document["live_after"], ["term-coordinator"])
        self.assertEqual(
            document["workers_before"][0]["terminal_state"], "reclaimable"
        )
        self.assertEqual(document["workers_after"][0]["terminal_state"], "released")

    def test_a_turn_that_still_owes_a_terminal_names_it_and_fails(self):
        record = self.settled(
            owed_after=[
                terminals.WorkerRow(
                    dispatch_id="ctx-one",
                    terminal="term-one",
                    terminal_state="reclaimable",
                )
            ]
        )
        self.assertEqual(record.status, status.PhaseStatus.FAILED)
        self.assertFalse(record.clean)
        self.assertIn("term-one", record.detail)

    def test_a_release_the_plane_could_not_verify_fails_the_record(self):
        owed = terminals.WorkerRow(dispatch_id="ctx-one", terminal="term-one")
        record = self.settled(
            dispositions=[
                terminals.judge_release(
                    role="implementer", row=owed, outcome="release_unknown"
                )
            ]
        )
        self.assertEqual(record.status, status.PhaseStatus.FAILED)
        self.assertIn("implementer", record.detail)

    def test_a_run_with_nothing_to_dispose_of_owes_nothing(self):
        record = self.settled(owed_before=[], dispositions=[])
        self.assertEqual(record.status, status.PhaseStatus.OK)
        self.assertFalse(record.distinguished)

    def test_the_record_serialises(self):
        json.dumps(self.settled().to_document())


if __name__ == "__main__":
    unittest.main()
