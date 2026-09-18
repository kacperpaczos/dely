"""A run that never finished leaves its state on the host, key material and all.

Cleanup is the last phase, so it only runs for a run that reached the end.
These are the instruments for the run that did not: that what it left is found
and named, that a private key among it is called one, and that nothing of it is
removed while anything still says the run might be alive.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cycle_runner import admission, cli, residue, status
from tests.test_config import minimal_document
from tests.test_lifecycle import make_source_repo

RUN = "20260916T101500Z-abcdef-00000001"
OTHER_RUN = "20260916T101600Z-abcdef-00000002"

PRIVATE_KEY = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
    b"c3RhbmQtaW4tZm9yLWEta2V5LXRoaXMtdGVzdC1uZXZlci1oYWQK\n"
    b"-----END OPENSSH PRIVATE KEY-----\n"
)
PUBLIC_HALF = b"ssh-ed25519 AAAAstandin dely-cycle\n"

#: What `existing_login` copies into every per-run home, in shape rather than
#: in substance. The runner's own auth receipt records `removed_after_run` as
#: false, so a killed run leaves this file sitting under the state root.
COPIED_LOGIN = (
    b'{"claudeAiOauth": {"accessToken": '
    b'"sk-ant-oat-stand-in-for-a-token-this-test-never-had", '
    b'"scopes": ["user:inference"]}}\n'
)

#: A file named as dully as anything else in an application profile, holding
#: the same token. A survey that reads names finds the first and not this.
DULL_NAME = b"last session: Bearer aGVsbG8tdGhpcy1pcy1uby1vbmVzLXRva2Vu\n"


def write(path: Path, content: bytes) -> Path:
    """Write one file, making the directories above it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class ResidueTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()

    def machine_run(self, run_id=RUN):
        """Lay out what the machine backend leaves: a transport key and a stack."""
        base = self.state / run_id
        write(base / "id_cycle", PRIVATE_KEY)
        write(base / "id_cycle.pub", PUBLIC_HALF)
        write(base / "known_hosts", b"192.0.2.10 ssh-ed25519 AAAAstandin\n")
        write(base / "stack" / "Pulumi.yaml", b"name: dely-cycle\n")
        return base

    def container_run(self, run_id=RUN):
        """Lay out what the container backend leaves: a per-run home."""
        base = self.state / run_id
        write(base / "distrobox.ini", b"[box]\nimage=ubuntu\n")
        write(base / "home" / "project" / "readme.md", b"project under test\n")
        write(base / "home" / ".claude" / ".credentials.json", b'{"ok": true}\n')
        return base

    def container_run_with_the_login_copied_in(self, run_id=RUN):
        """The container run as it really is: the operator's login copied in.

        The placeholder `container_run` writes is a file *called* credentials
        that holds none. This is the same path holding the real thing, which
        is what the auth method puts there on every run.
        """
        base = self.container_run(run_id)
        write(base / "home" / ".claude" / ".credentials.json", COPIED_LOGIN)
        return base


class SurveyTest(ResidueTestCase):
    def test_a_run_that_never_finished_is_found_with_what_it_left(self):
        self.machine_run()
        left = residue.survey(self.state, RUN)
        self.assertTrue(left.present)
        self.assertEqual(left.file_count, 4)
        self.assertEqual(left.path, self.state / RUN)

    def test_the_transport_key_is_named_as_key_material(self):
        self.machine_run()
        left = residue.survey(self.state, RUN)
        self.assertEqual(left.key_material, ("id_cycle",))
        self.assertTrue(left.carries_key_material)
        self.assertIn(residue.KEY_MATERIAL, left.headline())
        self.assertIn("id_cycle", left.headline())

    def test_a_count_of_directories_is_not_a_report(self):
        """The whole point of the report is that it says what was left."""
        self.machine_run()
        described = residue.survey(self.state, RUN).describe()
        self.assertIn("transport private key", described)
        self.assertIn(f"{residue.KEY_MATERIAL}: id_cycle", described)

    def test_key_material_is_decided_by_bytes_and_not_by_a_name(self):
        """A key under a dull name is a key; a dull file under `id_cycle` is not.

        This is the same rule the process survey follows: a name is what
        reaches something that was never this run's. Here it would also miss
        the thing it exists to find.
        """
        base = self.state / RUN
        write(base / "id_cycle", PUBLIC_HALF)
        write(base / "home" / ".config" / "somewhere" / "agent-identity", PRIVATE_KEY)
        left = residue.survey(self.state, RUN)
        self.assertEqual(
            left.key_material, ("home/.config/somewhere/agent-identity",)
        )
        self.assertNotIn("id_cycle", left.key_material)

    def test_the_login_a_killed_run_left_is_named_as_credential_material(self):
        """The container backend mints no key, and still leaves a secret.

        This is the case the rail was blind to. A real killed run on the
        Distrobox backend left 1003 files under the state root, the operator's
        own login byte-identical among them at mode 600, and the survey
        reported the host as carrying no key material — truthfully, about a
        question nobody had asked.
        """
        self.container_run_with_the_login_copied_in()
        left = residue.survey(self.state, RUN)
        self.assertEqual(
            left.credential_material, ("home/.claude/.credentials.json",)
        )
        self.assertTrue(left.carries_credential_material)
        self.assertTrue(left.carries_a_secret)
        self.assertEqual(left.key_material, ())
        self.assertIn(residue.CREDENTIAL_MATERIAL, left.headline())
        self.assertIn(".credentials.json", left.headline())

    def test_credential_material_is_decided_by_bytes_and_not_by_a_name(self):
        """The same rule as the key, and it cuts both ways here too."""
        base = self.state / RUN
        write(base / "home" / ".claude" / ".credentials.json", b'{"ok": true}\n')
        write(base / "home" / ".config" / "orca" / "recent-sessions", DULL_NAME)
        left = residue.survey(self.state, RUN)
        self.assertEqual(
            left.credential_material, ("home/.config/orca/recent-sessions",)
        )
        self.assertNotIn("home/.claude/.credentials.json", left.credential_material)

    def test_a_private_key_is_named_as_a_key_and_not_also_as_a_credential(self):
        """A key block is a credential shape too; it is reported once."""
        self.machine_run()
        left = residue.survey(self.state, RUN)
        self.assertEqual(left.key_material, ("id_cycle",))
        self.assertEqual(left.credential_material, ())

    def test_what_was_left_is_named_line_by_line_for_both_kinds(self):
        self.container_run_with_the_login_copied_in()
        described = residue.survey(self.state, RUN).describe()
        self.assertIn(
            f"{residue.CREDENTIAL_MATERIAL}: home/.claude/.credentials.json",
            described,
        )

    def test_a_run_that_left_no_secret_says_neither_word(self):
        self.container_run()
        left = residue.survey(self.state, RUN)
        self.assertFalse(left.carries_a_secret)
        self.assertNotIn(residue.CREDENTIAL_MATERIAL, left.headline())
        self.assertNotIn(residue.KEY_MATERIAL, left.headline())

    def test_the_container_backends_home_is_named_for_what_it_is(self):
        self.container_run()
        left = residue.survey(self.state, RUN)
        names = {entry.name: entry for entry in left.entries}
        self.assertIn("home", names)
        self.assertTrue(names["home"].is_directory)
        self.assertEqual(names["home"].file_count, 2)
        self.assertIn("per-run home", names["home"].role)
        self.assertIn("assemble manifest", names["distrobox.ini"].role)

    def test_a_backend_names_its_own_leftovers_and_only_its_own(self):
        self.assertEqual(
            residue.backend_that_wrote_it(residue.survey(self.state, RUN)), ""
        )
        self.machine_run()
        self.assertEqual(
            residue.backend_that_wrote_it(residue.survey(self.state, RUN)), "vm"
        )
        self.container_run(OTHER_RUN)
        self.assertEqual(
            residue.backend_that_wrote_it(residue.survey(self.state, OTHER_RUN)),
            "distrobox",
        )

    def test_a_link_is_counted_and_never_followed(self):
        """A link pointing out of the run is not a licence to read what it names."""
        outside = self.root / "elsewhere"
        write(outside / "operators_key", PRIVATE_KEY)
        base = self.state / RUN
        base.mkdir(parents=True)
        os.symlink(outside, base / "somewhere-else")
        left = residue.survey(self.state, RUN)
        self.assertEqual(left.links, ("somewhere-else",))
        self.assertEqual(left.key_material, ())

    def test_a_state_directory_that_is_a_link_is_not_walked(self):
        outside = self.root / "elsewhere"
        write(outside / "operators_key", PRIVATE_KEY)
        os.symlink(outside, self.state / RUN)
        left = residue.survey(self.state, RUN)
        self.assertFalse(left.present)
        self.assertEqual(left.key_material, ())
        self.assertIn("symbolic link", left.detail)

    def test_a_run_that_left_nothing_says_so(self):
        left = residue.survey(self.state, RUN)
        self.assertFalse(left.present)
        self.assertEqual(left.key_material, ())
        self.assertIn(RUN, left.detail)

    def test_a_walk_that_stopped_does_not_read_as_a_complete_one(self):
        self.container_run()
        left = residue.survey(self.state, RUN, max_entries=2)
        self.assertFalse(left.complete)
        self.assertIn("stopped", left.headline())
        self.assertTrue(residue.survey(self.state, RUN).complete)

    def test_a_name_this_runner_never_minted_is_not_walked(self):
        write(self.root / "outside" / "id_cycle", PRIVATE_KEY)
        left = residue.survey(self.state, "../outside")
        self.assertFalse(left.present)
        self.assertEqual(left.key_material, ())
        self.assertIn("run identifier", left.detail)

    def test_the_document_carries_the_credential_as_well_as_the_key(self):
        self.container_run_with_the_login_copied_in()
        document = residue.survey(self.state, RUN).to_document()
        self.assertTrue(document["carries_credential_material"])
        self.assertTrue(document["carries_a_secret"])
        self.assertFalse(document["carries_key_material"])
        self.assertEqual(
            document["credential_material"], ["home/.claude/.credentials.json"]
        )
        home = [item for item in document["entries"] if item["name"] == "home"][0]
        self.assertEqual(
            home["credential_material"], ["home/.claude/.credentials.json"]
        )

    def test_the_document_carries_what_the_report_says(self):
        self.machine_run()
        document = residue.survey(self.state, RUN).to_document()
        self.assertTrue(document["carries_key_material"])
        self.assertEqual(document["key_material"], ["id_cycle"])
        self.assertEqual(json.loads(json.dumps(document))["run_id"], RUN)


class RunIdentifiersTest(ResidueTestCase):
    def test_every_run_shaped_directory_is_found_lease_or_no_lease(self):
        self.machine_run()
        self.container_run(OTHER_RUN)
        (self.state / "leases" / "vm").mkdir(parents=True)
        (self.state / "not-a-run").mkdir()
        self.assertEqual(residue.run_identifiers(self.state), [RUN, OTHER_RUN])

    def test_a_state_root_that_does_not_exist_yet_holds_nothing(self):
        self.assertEqual(residue.run_identifiers(self.root / "absent"), [])


class DiscardTest(ResidueTestCase):
    def test_state_is_removed_once_nothing_of_the_run_is_left(self):
        base = self.machine_run()
        removed = residue.discard(root=self.state, run_id=RUN)
        self.assertFalse(base.exists())
        self.assertEqual(removed.key_material, ("id_cycle",))

    def test_a_process_still_holding_a_path_refuses(self):
        base = self.machine_run()
        with self.assertRaises(residue.ResidueRefused) as refusal:
            residue.discard(
                root=self.state, run_id=RUN, holders=["pid 4242 orca-ide"]
            )
        self.assertIn("still has processes", str(refusal.exception))
        self.assertTrue((base / "id_cycle").is_file())

    def test_an_environment_still_on_the_host_refuses(self):
        base = self.machine_run()
        with self.assertRaises(residue.ResidueRefused) as refusal:
            residue.discard(
                root=self.state,
                run_id=RUN,
                still_present=["domain:dely-cycle-standin"],
            )
        self.assertIn("is not gone", str(refusal.exception))
        self.assertTrue((base / "id_cycle").is_file())

    def test_a_question_this_host_could_not_ask_refuses(self):
        base = self.machine_run()
        with self.assertRaises(residue.ResidueRefused) as refusal:
            residue.discard(
                root=self.state,
                run_id=RUN,
                unanswered=["virsh is not on this host's path"],
            )
        self.assertIn("was told no to", str(refusal.exception))
        self.assertTrue((base / "id_cycle").is_file())

    def test_a_name_this_runner_never_minted_is_refused(self):
        write(self.root / "outside" / "id_cycle", PRIVATE_KEY)
        with self.assertRaises(residue.ResidueRefused) as refusal:
            residue.discard(root=self.state, run_id="../outside")
        self.assertIn("run identifier", str(refusal.exception))
        self.assertTrue((self.root / "outside" / "id_cycle").is_file())

    def test_a_run_that_left_nothing_is_not_a_removal(self):
        with self.assertRaises(residue.ResidueRefused) as refusal:
            residue.discard(root=self.state, run_id=RUN)
        self.assertIn("left nothing", str(refusal.exception))

    def test_the_lease_store_is_never_what_gets_removed(self):
        self.machine_run()
        (self.state / "leases" / "vm").mkdir(parents=True)
        residue.discard(root=self.state, run_id=RUN)
        self.assertTrue((self.state / "leases" / "vm").is_dir())


class LeaseNamesWhatTheRunLeftTest(ResidueTestCase):
    """The operator meets the dead run at its lease, so the lease says it."""

    def leases(self, live=()):
        return admission.read_leases(
            self.state,
            "vm",
            boot_digest="one-boot",
            prober=lambda pid: dict(live).get(pid, ""),
        )

    def take(self, run_id=RUN, pid=4242):
        return admission.acquire(
            root=self.state,
            run_id=run_id,
            backend="vm",
            limits=admission.Limits(),
            claim=admission.Claim(),
            pid=pid,
            boot_digest="one-boot",
            prober=lambda _: "live",
            wait_seconds=0.5,
            sleeper=lambda _: None,
        )

    def test_an_orphaned_lease_names_the_key_its_run_left(self):
        self.take()
        self.machine_run()
        record = self.leases()[0]
        self.assertEqual(record.state, admission.ORPHANED)
        self.assertTrue(record.state_residue.carries_key_material)
        self.assertIn(residue.KEY_MATERIAL, record.describe())
        self.assertIn("id_cycle", record.describe())

    def test_an_orphaned_lease_names_the_login_its_run_left(self):
        """The container backend leaves no key, so this is what its lease says."""
        self.take()
        self.container_run_with_the_login_copied_in()
        record = self.leases()[0]
        self.assertEqual(record.state, admission.ORPHANED)
        self.assertTrue(record.state_residue.carries_credential_material)
        self.assertIn(residue.CREDENTIAL_MATERIAL, record.describe())
        self.assertIn(".credentials.json", record.describe())

    def test_a_lease_with_a_live_owner_is_not_surveyed(self):
        """A held lease is a run still using its state, not one that left it."""
        self.take()
        self.machine_run()
        record = self.leases(live=((4242, "live"),))[0]
        self.assertEqual(record.state, admission.HELD)
        self.assertIsNone(record.state_residue)

    def test_the_lease_document_carries_what_was_left(self):
        self.take()
        self.machine_run()
        document = self.leases()[0].to_document()
        self.assertTrue(document["state_residue"]["carries_key_material"])

    def test_a_dead_run_that_left_nothing_adds_nothing_to_the_sentence(self):
        self.take()
        record = self.leases()[0]
        self.assertFalse(record.state_residue.present)
        self.assertNotIn(residue.KEY_MATERIAL, record.describe())


class ResidueCommandTest(ResidueTestCase):
    """The operator decides: a command they run, over state nobody else owns."""

    def setUp(self):
        super().setUp()
        self.repo = make_source_repo(self.root / "src")

    def write_config(self, backend="distrobox"):
        document = minimal_document()
        document["artifact_root"] = str(self.root / "artifacts")
        document["state_root"] = str(self.state)
        document["project"]["source"] = str(self.repo)
        document["project"]["revision"] = "main"
        if backend == "vm":
            document.pop("distrobox")
            document["backend"] = "vm"
            document["vm"] = {
                "provider": "pulumi-libvirt",
                "provider_version": "0.5.3",
                "stack_prefix": "dely-cycle",
                "base_image": str(self.root / "base.qcow2"),
                "base_image_sha256": "0" * 64,
                "guest_user": "cycle",
                "venv": str(self.root / "venv"),
            }
        path = self.root / f"{backend}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def invoke(self, arguments):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(arguments, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_a_host_with_nothing_left_on_it_says_so(self):
        code, out, _ = self.invoke(["residue", "--config", str(self.write_config())])
        self.assertEqual(code, 0)
        self.assertIn("no run has left state", out)

    def test_what_a_dead_run_left_is_reported_and_the_key_is_named(self):
        self.machine_run()
        code, out, _ = self.invoke(
            ["residue", "--config", str(self.write_config("vm"))]
        )
        self.assertEqual(code, status.exit_code(status.RunStatus.BLOCKED))
        self.assertIn(RUN, out)
        self.assertIn(residue.KEY_MATERIAL, out)
        self.assertIn("id_cycle", out)
        self.assertIn("carrying private key material", out)

    def test_the_login_a_dead_container_run_left_is_reported_and_counted(self):
        """The closing count is the verdict an operator reads about the host.

        Counting only private key material made it structurally zero on the
        backend that mints no key and copies a login every run, so the line
        that should have been a warning read as an all-clear.
        """
        self.container_run_with_the_login_copied_in()
        code, out, _ = self.invoke(
            ["residue", "--config", str(self.write_config("distrobox"))]
        )
        self.assertEqual(code, status.exit_code(status.RunStatus.BLOCKED))
        self.assertIn(residue.CREDENTIAL_MATERIAL, out)
        self.assertIn("home/.claude/.credentials.json", out)
        self.assertIn("1 of them carrying credential material", out)

    def test_a_removal_receipt_names_the_credential_that_went_with_it(self):
        self.container_run_with_the_login_copied_in()
        code, out, err = self.invoke(
            [
                "residue",
                "--config",
                str(self.write_config("distrobox")),
                "--run-id",
                RUN,
                "--discard",
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertIn(
            f"including {residue.CREDENTIAL_MATERIAL} at "
            "home/.claude/.credentials.json",
            out,
        )
        self.assertFalse((self.state / RUN).exists())

    def test_the_machine_readable_form_carries_the_credential_too(self):
        self.container_run_with_the_login_copied_in()
        _, out, _ = self.invoke(
            ["residue", "--config", str(self.write_config("distrobox")), "--json"]
        )
        document = json.loads(out)
        self.assertTrue(document[0]["state"]["carries_credential_material"])
        self.assertFalse(document[0]["state"]["carries_key_material"])

    def test_the_machine_readable_form_carries_the_same_fact(self):
        self.machine_run()
        _, out, _ = self.invoke(
            ["residue", "--config", str(self.write_config("vm")), "--json"]
        )
        document = json.loads(out)
        self.assertEqual(document[0]["run_id"], RUN)
        self.assertTrue(document[0]["state"]["carries_key_material"])

    def test_discarding_needs_one_named_run(self):
        self.machine_run()
        code, _, err = self.invoke(
            ["residue", "--config", str(self.write_config("vm")), "--discard"]
        )
        self.assertEqual(code, status.exit_code(status.RunStatus.BLOCKED))
        self.assertIn("needs --run-id", err)
        self.assertTrue((self.state / RUN / "id_cycle").is_file())

    def test_a_run_whose_lease_says_it_is_running_keeps_what_it_has(self):
        self.machine_run()
        admission.acquire(
            root=self.state,
            run_id=RUN,
            backend="vm",
            limits=admission.Limits(),
            claim=admission.Claim(),
            pid=os.getpid(),
            wait_seconds=0.5,
            sleeper=lambda _: None,
        )
        code, _, err = self.invoke(
            [
                "residue",
                "--config",
                str(self.write_config("vm")),
                "--run-id",
                RUN,
                "--discard",
            ]
        )
        self.assertEqual(code, status.exit_code(status.RunStatus.BLOCKED))
        self.assertIn("is not gone", err)
        self.assertTrue((self.state / RUN / "id_cycle").is_file())

    def test_state_the_other_backend_wrote_is_not_a_question_this_one_answered(self):
        self.machine_run()
        code, _, err = self.invoke(
            [
                "residue",
                "--config",
                str(self.write_config("distrobox")),
                "--run-id",
                RUN,
                "--discard",
            ]
        )
        self.assertEqual(code, status.exit_code(status.RunStatus.BLOCKED))
        self.assertIn("written by the vm backend", err)
        self.assertTrue((self.state / RUN / "id_cycle").is_file())

    def test_a_run_that_left_nothing_is_named_rather_than_counted(self):
        code, out, _ = self.invoke(
            [
                "residue",
                "--config",
                str(self.write_config()),
                "--run-id",
                OTHER_RUN,
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn(OTHER_RUN, out)


if __name__ == "__main__":
    unittest.main()
