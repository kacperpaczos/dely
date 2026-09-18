"""Removing the container is not ending what the run started inside it."""

import unittest

from cycle_runner import processes

RUN_PATH = "/var/tmp/dely-cycle/state/20260915T000000Z-aaaaaa-00000001"


def process(pid, command):
    return processes.Process(pid=pid, command=command)


class Table:
    """A stand-in process table that a signal really changes."""

    def __init__(self, rows, stubborn=()):
        self.rows = list(rows)
        self.stubborn = set(stubborn)
        self.signalled = []

    def lister(self):
        return list(self.rows)

    def signaller(self, pid, number):
        self.signalled.append((pid, number))
        if pid in self.stubborn and number != 9:
            return
        self.rows = [row for row in self.rows if row.pid != pid]


class SurveyTest(unittest.TestCase):
    def setUp(self):
        self.slept = []

    def run_survey(self, table, **options):
        return processes.survey_and_stop(
            [RUN_PATH],
            lister=table.lister,
            signaller=table.signaller,
            sleeper=self.slept.append,
            **options,
        )

    def test_a_process_holding_this_runs_path_is_found_and_stopped(self):
        table = Table([
            process(101, f"/opt/Orca/orca-ide --user-data-dir={RUN_PATH}/home/.config/orca"),
            process(102, "/opt/Orca/orca-ide --user-data-dir=/home/someone/.config/orca"),
        ])
        report = self.run_survey(table)
        self.assertEqual(report.found, [101])
        self.assertEqual(report.stopped, [101])
        self.assertTrue(report.clean)

    def test_the_operators_own_application_is_never_touched(self):
        table = Table([
            process(201, "/opt/Orca/orca-ide --user-data-dir=/home/someone/.config/orca"),
            process(202, "/opt/Orca/orca-ide"),
        ])
        report = self.run_survey(table)
        self.assertEqual(report.found, [])
        self.assertEqual(table.signalled, [])
        self.assertIn("no process", report.detail)

    def test_a_process_that_ignores_the_first_signal_is_killed(self):
        table = Table([process(301, f"bash --rcfile {RUN_PATH}/home/.bashrc")], stubborn={301})
        report = self.run_survey(table)
        self.assertEqual([number for _pid, number in table.signalled], [15, 9])
        self.assertTrue(report.clean)

    def test_a_process_that_will_not_stop_is_reported_as_surviving(self):
        class Unkillable(Table):
            def signaller(self, pid, number):
                self.signalled.append((pid, number))

        table = Unkillable([process(401, f"orca-ide --user-data-dir={RUN_PATH}/x")])
        report = self.run_survey(table)
        self.assertEqual(report.surviving, [401])
        self.assertFalse(report.clean)
        self.assertIn("would not stop", report.detail)

    def test_the_survey_never_signals_the_runner_itself(self):
        import os

        table = Table([process(os.getpid(), f"python3 run-cycle {RUN_PATH}")])
        report = self.run_survey(table)
        self.assertEqual(report.found, [])
        self.assertEqual(table.signalled, [])

    def test_an_empty_path_list_surveys_nothing(self):
        table = Table([process(501, f"orca-ide {RUN_PATH}")])
        report = processes.survey_and_stop(
            [""], lister=table.lister, signaller=table.signaller, sleeper=self.slept.append
        )
        self.assertEqual(report.found, [])
        self.assertEqual(table.signalled, [])


if __name__ == "__main__":
    unittest.main()


class ParentWithoutAMarkerTest(unittest.TestCase):
    """The application this runner starts names no path in its own argv."""

    def setUp(self):
        self.slept = []

    def table(self):
        return Table([
            # As it really appears: the parent names nothing, the children do.
            process(900, "/opt/Orca/orca-ide --disable-gpu"),
            process(901, f"/opt/Orca/orca-ide --type=gpu-process --user-data-dir={RUN_PATH}/home/.config/orca"),
            process(902, "/opt/Orca/orca-ide parcel-watcher-process-entry.js"),
            process(999, "/opt/Orca/orca-ide"),
        ])

    def test_a_parent_that_names_nothing_is_left_when_only_argv_is_read(self):
        table = self.table()
        found = processes.holding([RUN_PATH], lister=table.lister)
        self.assertEqual([p.pid for p in found], [901])

    def test_its_home_ties_it_to_the_run(self):
        table = Table([
            processes.Process(
                pid=900,
                command="/opt/Orca/orca-ide --disable-gpu",
                home=f"{RUN_PATH}/home",
            ),
            processes.Process(pid=999, command="/opt/Orca/orca-ide", home="/home/someone"),
        ])
        found = processes.holding([RUN_PATH], lister=table.lister)
        self.assertEqual([p.pid for p in found], [900])

    def test_a_recorded_identifier_takes_its_whole_tree(self):
        table = Table([
            processes.Process(pid=900, command="/opt/Orca/orca-ide --disable-gpu", parent=1),
            processes.Process(pid=901, command="helper", parent=900),
            processes.Process(pid=902, command="helper of a helper", parent=901),
            processes.Process(pid=999, command="/opt/Orca/orca-ide", parent=1),
        ])
        found = processes.holding([], lister=table.lister, roots=[900])
        self.assertEqual([p.pid for p in found], [900, 901, 902])

    def test_no_recorded_identifier_means_no_tree_is_taken(self):
        table = Table([
            processes.Process(pid=900, command="/opt/Orca/orca-ide --disable-gpu", parent=1),
            processes.Process(pid=901, command="helper", parent=900),
        ])
        self.assertEqual(processes.holding([], lister=table.lister), [])

    def test_the_runner_is_never_a_root(self):
        import os

        table = Table([
            processes.Process(pid=os.getpid(), command="the runner", parent=1),
            processes.Process(pid=2, command="a child of the runner", parent=os.getpid()),
        ])
        self.assertEqual(processes.holding([], lister=table.lister, roots=[os.getpid()]), [])


class MountNamespaceTest(unittest.TestCase):
    """What tells a process inside the environment from the plumbing outside it."""

    HOST = "mnt:[4026531832]"
    BOX = "mnt:[4026532999]"

    def test_a_different_namespace_is_inside(self):
        self.assertTrue(
            processes.Process(pid=1, command="a", namespace=self.BOX).inside(self.HOST)
        )

    def test_the_same_namespace_is_not(self):
        self.assertFalse(
            processes.Process(pid=1, command="a", namespace=self.HOST).inside(self.HOST)
        )

    def test_an_unknown_namespace_is_not_claimed_either_way(self):
        self.assertFalse(processes.Process(pid=1, command="a").inside(self.HOST))
        self.assertFalse(
            processes.Process(pid=1, command="a", namespace=self.BOX).inside("")
        )

    def test_this_process_reports_a_namespace_on_this_host(self):
        self.assertTrue(processes.mount_namespace().startswith("mnt:["))

    def test_a_process_that_is_gone_reports_nothing(self):
        self.assertEqual(processes.mount_namespace(999999999), "")


class UnreadableProcessTest(unittest.TestCase):
    """A process this host would not let the survey read is not a no-match.

    Every process the operator owns is readable here. The ones that are not
    are running as somebody else — in a box that shares this process table,
    that is the box's own `sudo`, mapped to a subordinate identifier. Such a
    process cannot be shown to be this run's and cannot be shown not to be,
    and a survey that treats it as unrelated reports a clean host over a
    process it never managed to look at.
    """

    def setUp(self):
        self.slept = []

    def table(self):
        return Table([
            processes.Process(
                pid=700,
                command="sh -c apt-get update",
                parent=1,
                home=f"{RUN_PATH}/home",
            ),
            processes.Process(
                pid=701,
                command="sudo apt-get update",
                parent=700,
                unreadable=("its environment", "its working directory"),
            ),
            processes.Process(
                pid=800,
                command="/usr/lib/systemd/systemd-journald",
                parent=1,
                unreadable=("its environment", "its working directory"),
            ),
        ])

    def test_a_child_this_host_will_not_let_it_read_is_named(self):
        owned = processes.attribute([RUN_PATH], lister=self.table().lister)
        self.assertEqual(owned.pids, [700])
        self.assertEqual([item.pid for item in owned.unattributed], [701])
        self.assertFalse(owned.settled)

    def test_why_it_could_not_be_decided_is_said_rather_than_implied(self):
        owned = processes.attribute([RUN_PATH], lister=self.table().lister)
        said = owned.unattributed[0].describe()
        self.assertIn("its parent pid 700 is this run's", said)
        self.assertIn("its environment", said)

    def test_an_unreadable_process_with_no_tie_to_the_run_is_left_alone(self):
        """Most of a host is unreadable and none of it is this run's."""
        owned = processes.attribute([RUN_PATH], lister=self.table().lister)
        self.assertNotIn(800, owned.pids)
        self.assertNotIn(800, [item.pid for item in owned.unattributed])

    def test_nothing_at_all_is_signalled_while_one_cannot_be_decided(self):
        table = self.table()
        report = processes.survey_and_stop(
            [RUN_PATH],
            lister=table.lister,
            signaller=table.signaller,
            sleeper=self.slept.append,
        )
        self.assertEqual(table.signalled, [])
        self.assertEqual(report.stopped, [])
        self.assertEqual(report.unattributed, [701])
        self.assertFalse(report.clean)
        self.assertIn("nothing was signalled", report.detail)

    def test_the_refusal_still_names_what_it_would_have_signalled(self):
        table = self.table()
        report = processes.survey_and_stop(
            [RUN_PATH],
            lister=table.lister,
            signaller=table.signaller,
            sleeper=self.slept.append,
        )
        self.assertEqual(report.found, [700])
        self.assertIn(700, report.attribution)
        self.assertIn(701, report.unreadable)

    def test_a_readable_child_that_names_nothing_is_not_walked_through(self):
        """Being near one of this run's processes is not being one of them."""
        table = Table([
            processes.Process(
                pid=700, command="the run's own", parent=1, home=f"{RUN_PATH}/home"
            ),
            processes.Process(pid=702, command="a host helper", parent=700),
            processes.Process(
                pid=703,
                command="something of somebody else's",
                parent=702,
                unreadable=("its environment",),
            ),
        ])
        owned = processes.attribute([RUN_PATH], lister=table.lister)
        self.assertEqual(owned.pids, [700])
        self.assertEqual(owned.unattributed, ())


class SignalRefusedTest(unittest.TestCase):
    """A signal the host refused is not a stop, and must not read as one."""

    def setUp(self):
        self.slept = []

    def test_a_process_this_runner_may_not_signal_is_reported_as_surviving(self):
        class Forbidden(Table):
            def signaller(self, pid, number):
                self.signalled.append((pid, number))
                raise PermissionError(1, "Operation not permitted")

        table = Forbidden([
            processes.Process(pid=910, command="a box's own root", home=f"{RUN_PATH}/home")
        ])
        report = processes.survey_and_stop(
            [RUN_PATH],
            lister=table.lister,
            signaller=table.signaller,
            sleeper=self.slept.append,
        )
        self.assertEqual(report.surviving, [910])
        self.assertEqual(report.stopped, [])
        self.assertIn("PermissionError", report.refused[910])
        self.assertIn("refused the signal", report.detail)
        self.assertFalse(report.clean)

    def test_a_process_that_had_already_gone_is_not_a_refusal(self):
        class Gone(Table):
            def signaller(self, pid, number):
                self.signalled.append((pid, number))
                self.rows = [row for row in self.rows if row.pid != pid]
                raise ProcessLookupError(3, "No such process")

        table = Gone([
            processes.Process(pid=911, command="already going", home=f"{RUN_PATH}/home")
        ])
        report = processes.survey_and_stop(
            [RUN_PATH],
            lister=table.lister,
            signaller=table.signaller,
            sleeper=self.slept.append,
        )
        self.assertEqual(report.stopped, [911])
        self.assertEqual(report.refused, {})
        self.assertTrue(report.clean)


class NamespaceIsEstablishedFromWhereAProcessIsTest(unittest.TestCase):
    """Which namespace is the run's, and which is the container manager's.

    Measured on this host: a rootless container manager runs its client and
    its monitor in one namespace of their own, shared by every box that user
    has — the operator's included. Those processes carry this run's paths on
    their command lines, because the paths are what they were told to mount.
    Seeding from a command line would take the operator's boxes as this run's.
    """

    HOST = "mnt:[4026531832]"
    MANAGER = "mnt:[4026533622]"
    BOX = "mnt:[4026533804]"

    def table(self):
        return Table([
            # The container manager's client, holding this run's paths because
            # they are what it was asked to mount, in the namespace it shares
            # with every other box on this host.
            processes.Process(
                pid=1001,
                command=f"podman exec --env=HOME={RUN_PATH}/home dely-cycle",
                home="/home/someone",
                cwd="/home/someone",
                namespace=self.MANAGER,
            ),
            # The operator's own box, monitored from that same namespace.
            processes.Process(
                pid=1002,
                command="conmon -n claude-desktop",
                home="/home/someone",
                namespace=self.MANAGER,
            ),
            # Inside this run's box: its home is where it actually is.
            processes.Process(
                pid=1003,
                command="/opt/Orca/orca-ide --disable-gpu",
                home=f"{RUN_PATH}/home",
                namespace=self.BOX,
            ),
            # Also inside it, and naming nothing of its own.
            processes.Process(
                pid=1004, command="claude --dangerously-skip-permissions",
                namespace=self.BOX,
            ),
        ])

    def test_a_command_line_does_not_establish_a_namespace_as_this_runs(self):
        owned = processes.attribute(
            [RUN_PATH], lister=self.table().lister, host_namespace=self.HOST
        )
        self.assertNotIn(1002, owned.pids)

    def test_a_home_does_establish_it_and_takes_what_shares_it(self):
        owned = processes.attribute(
            [RUN_PATH], lister=self.table().lister, host_namespace=self.HOST
        )
        self.assertIn(1004, owned.pids)
        reason = next(item for item in owned.own if item.pid == 1004).reasons[0]
        self.assertIn(self.BOX, reason)

    def test_the_manager_that_named_the_path_is_still_this_runs_business(self):
        """It is attributed, but by its own argv rather than by a namespace."""
        owned = processes.attribute(
            [RUN_PATH], lister=self.table().lister, host_namespace=self.HOST
        )
        reasons = next(item for item in owned.own if item.pid == 1001).reasons
        self.assertEqual(reasons, (f"its command line names {RUN_PATH}",))
