"""One host, one environment per backend, and nobody reclaims a slot for free."""

import json
import subprocess
import sys
import time
import tempfile
import textwrap
import unittest
from pathlib import Path

from cycle_runner import admission

RUN_ONE = "20260916T090000Z-aaaaaa-00000001"
RUN_TWO = "20260916T090100Z-aaaaaa-00000002"
RUN_THREE = "20260916T090200Z-aaaaaa-00000003"

BOOT = "boot-digest-one"
OTHER_BOOT = "boot-digest-two"

SMALL = admission.Claim(vcpus=2, memory_mb=4096, pids=2048, disk_bytes=1024, timeout_seconds=600)
BUDGET = admission.Budget(
    vcpus=4, memory_mb=8192, pids=4096, disk_bytes=4096, timeout_seconds=1800
)


class Table:
    """A process table the test decides the contents of."""

    def __init__(self, live=((1234, "sig-1234"),)):
        self.live = dict(live)

    def prober(self, pid):
        return self.live.get(pid, "")

    def kill(self, pid):
        self.live.pop(pid, None)


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.table = Table()

    def acquire(self, run_id, *, pid=1234, limits=None, claim=SMALL, boot=BOOT, backend="distrobox"):
        return admission.acquire(
            root=self.root,
            run_id=run_id,
            backend=backend,
            limits=limits or admission.Limits(),
            claim=claim,
            pid=pid,
            boot_digest=boot,
            prober=self.table.prober,
            wait_seconds=0.5,
            sleeper=lambda _: None,
        )

    def leases(self, backend="distrobox"):
        return admission.read_leases(
            self.root, backend, boot_digest=BOOT, prober=self.table.prober
        )

    # -- the ceiling ------------------------------------------------------

    def test_one_environment_per_backend_is_the_default(self):
        self.acquire(RUN_ONE)
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(RUN_TWO)
        self.assertIn("allows 1 active environment", str(refusal.exception))
        self.assertIn(RUN_ONE, str(refusal.exception))

    def test_the_other_backend_keeps_its_own_slot(self):
        self.acquire(RUN_ONE, backend="distrobox")
        lease = self.acquire(RUN_TWO, backend="vm")
        self.assertEqual(lease.run_id, RUN_TWO)

    def test_a_raised_ceiling_without_the_switch_is_refused_in_configuration(self):
        # The switch is what raises it; a number on its own does not.
        limits = admission.Limits(parallel=False, max_active=3, budget=BUDGET)
        self.assertEqual(limits.effective_max_active, 1)
        self.acquire(RUN_ONE, limits=limits)
        with self.assertRaises(admission.AdmissionRefused):
            self.acquire(RUN_TWO, pid=1235, limits=limits)

    def test_parallel_admits_up_to_its_ceiling_and_no_further(self):
        limits = admission.Limits(parallel=True, max_active=2, budget=BUDGET)
        self.table.live[1235] = "sig-1235"
        self.acquire(RUN_ONE, pid=1234, limits=limits)
        self.acquire(RUN_TWO, pid=1235, limits=limits)
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(RUN_THREE, pid=1236, limits=limits)
        self.assertIn("allows 2 active environment", str(refusal.exception))

    def test_parallel_without_a_budget_is_an_unbounded_spawn(self):
        limits = admission.Limits(parallel=True, max_active=4, budget=None)
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(RUN_ONE, limits=limits)
        self.assertIn("unbounded spawn", str(refusal.exception))

    # -- the budget -------------------------------------------------------

    def test_the_budget_sums_across_the_runs_already_holding_a_slot(self):
        limits = admission.Limits(parallel=True, max_active=4, budget=BUDGET)
        self.table.live[1235] = "sig-1235"
        self.acquire(RUN_ONE, pid=1234, limits=limits, claim=SMALL)
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(
                RUN_TWO,
                pid=1235,
                limits=limits,
                claim=admission.Claim(vcpus=3, memory_mb=1, pids=1, disk_bytes=1),
            )
        self.assertIn("5 virtual cpus against a budget of 4", str(refusal.exception))

    def test_a_deadline_beyond_the_budget_is_refused(self):
        limits = admission.Limits(parallel=True, max_active=2, budget=BUDGET)
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(
                RUN_ONE,
                limits=limits,
                claim=admission.Claim(timeout_seconds=BUDGET.timeout_seconds + 1),
            )
        self.assertIn("deadline", str(refusal.exception))

    # -- nobody reclaims a slot for free ----------------------------------

    def test_a_lease_whose_owner_died_still_holds_the_slot(self):
        self.acquire(RUN_ONE, pid=1234)
        self.table.kill(1234)
        self.assertEqual([record.state for record in self.leases()], [admission.ORPHANED])
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(RUN_TWO, pid=9999)
        self.assertIn("no live owner", str(refusal.exception))

    def test_a_recycled_pid_is_not_the_owner(self):
        self.acquire(RUN_ONE, pid=1234)
        self.table.live[1234] = "a-different-start-time"
        self.assertEqual([record.state for record in self.leases()], [admission.ORPHANED])

    def test_a_lease_from_an_earlier_boot_is_orphaned(self):
        self.acquire(RUN_ONE, pid=1234, boot=OTHER_BOOT)
        self.assertEqual([record.state for record in self.leases()], [admission.ORPHANED])

    def test_a_lease_this_runner_cannot_read_still_holds_the_slot(self):
        self.acquire(RUN_ONE)
        path = admission.lease_directory(self.root, "distrobox") / f"{RUN_ONE}.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual([record.state for record in self.leases()], [admission.UNREADABLE])
        with self.assertRaises(admission.AdmissionRefused):
            self.acquire(RUN_TWO)

    def test_a_run_identifier_is_used_once(self):
        self.acquire(RUN_ONE)
        admission.release(
            admission.Lease(record=self.leases()[0]), confirmed=False, reason="left over"
        )
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(RUN_ONE)
        self.assertIn("used once", str(refusal.exception))

    # -- releasing --------------------------------------------------------

    def test_a_confirmed_cleanup_gives_the_slot_back(self):
        lease = self.acquire(RUN_ONE)
        admission.release(lease, confirmed=True, reason="destroyed")
        self.assertEqual(self.leases(), [])
        self.assertEqual(self.acquire(RUN_TWO).run_id, RUN_TWO)

    def test_an_unconfirmed_cleanup_keeps_the_slot_and_says_why(self):
        lease = self.acquire(RUN_ONE)
        admission.release(lease, confirmed=False, reason="a process outlived the run")
        records = self.leases()
        self.assertEqual([record.state for record in records], [admission.RETAINED])
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            self.acquire(RUN_TWO)
        self.assertIn("a process outlived the run", str(refusal.exception))

    def test_clearing_refuses_while_the_run_still_has_processes(self):
        self.acquire(RUN_ONE)
        with self.assertRaises(admission.AdmissionRefused) as refusal:
            admission.clear(
                root=self.root,
                backend="distrobox",
                run_id=RUN_ONE,
                holders=["pid 4242 orca-ide"],
            )
        self.assertIn("still has processes", str(refusal.exception))
        self.assertEqual(len(self.leases()), 1)

    def test_clearing_removes_the_slot_once_nothing_is_left(self):
        self.acquire(RUN_ONE)
        admission.clear(root=self.root, backend="distrobox", run_id=RUN_ONE, holders=[])
        self.assertEqual(self.leases(), [])


class LockIsAcrossProcessesTest(unittest.TestCase):
    """The count has to be taken under a lock two separate runners share.

    Racing two runners does not show this: they serialise by luck, because the
    stretch between counting and writing is shorter than the jitter between two
    interpreters starting. So the test holds the lock for a visible length of
    time from another process and asks whether this one waits for it.
    """

    HOLDER = textwrap.dedent(
        """
        import sys, time
        sys.path.insert(0, {package!r})
        from cycle_runner import admission

        lock_path, marker, seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
        from pathlib import Path
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        with admission._Guard(Path(lock_path), wait_seconds=5.0, sleeper=time.sleep):
            Path(marker).write_text("held")
            time.sleep(seconds)
        """
    )

    CONTENDER = textwrap.dedent(
        """
        import json, sys, time
        sys.path.insert(0, {package!r})
        from cycle_runner import admission

        root, run_id, barrier = sys.argv[1], sys.argv[2], sys.argv[3]
        target = float(barrier)
        while time.time() < target:
            time.sleep(0.005)
        try:
            admission.acquire(
                root=root,
                run_id=run_id,
                backend="distrobox",
                limits=admission.Limits(),
                claim=admission.Claim(vcpus=1, timeout_seconds=60),
                wait_seconds=5.0,
            )
            print(json.dumps({{"granted": True}}))
        except admission.AdmissionRefused as refusal:
            print(json.dumps({{"granted": False, "why": str(refusal)}}))
        """
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.package = str(Path(__file__).resolve().parent.parent)

    def script(self, name, body):
        path = self.directory / name
        path.write_text(body.format(package=self.package), encoding="utf-8")
        return path

    def test_a_runner_waits_for_a_lock_another_process_already_holds(self):
        root = self.directory / "state"
        lock = root / "leases" / "distrobox.lock"
        marker = self.directory / "held"
        holder = subprocess.Popen(
            [sys.executable, str(self.script("holder.py", self.HOLDER)),
             str(lock), str(marker), "4"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        def finish():
            if holder.poll() is None:
                holder.kill()
            holder.communicate()

        self.addCleanup(finish)
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            if holder.poll() is not None:
                self.fail(f"the holder exited early: {holder.communicate()}")
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "the other process never took the lock")

        with self.assertRaises(admission.AdmissionRefused) as refusal:
            admission.acquire(
                root=root,
                run_id=RUN_ONE,
                backend="distrobox",
                limits=admission.Limits(),
                claim=SMALL,
                wait_seconds=0.5,
            )
        self.assertIn("admission lock", str(refusal.exception))
        self.assertEqual(
            list(admission.lease_directory(root, "distrobox").glob("*.json")),
            [],
            "a runner that could not take the lock must not have written a lease",
        )

    def test_two_runners_starting_together_do_not_both_get_the_slot(self):
        root = self.directory / "race"
        script = self.script("contender.py", self.CONTENDER)
        barrier = f"{time.time() + 1.5:.3f}"
        children = [
            subprocess.Popen(
                [sys.executable, str(script), str(root), run_id, barrier],
                stdout=subprocess.PIPE,
                text=True,
            )
            for run_id in (RUN_ONE, RUN_TWO)
        ]
        verdicts = []
        for child in children:
            stdout, _ = child.communicate(timeout=60)
            verdicts.append(json.loads(stdout.strip().splitlines()[-1]))
        granted = [verdict for verdict in verdicts if verdict["granted"]]
        self.assertEqual(
            len(granted),
            1,
            f"exactly one of two simultaneous runners may hold the slot: {verdicts}",
        )


if __name__ == "__main__":
    unittest.main()
