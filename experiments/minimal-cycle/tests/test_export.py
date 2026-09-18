"""The export receipt is a re-read of the host, not a memory of the write."""

import json
import tempfile
import unittest
from pathlib import Path

from cycle_runner import export, redact, status


class ExporterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "run"
        self.exporter = export.Exporter(self.root)
        self.addCleanup(self._tmp.cleanup)

    def test_a_written_artifact_lands_on_the_host(self):
        self.exporter.write_text("check.stdout", "marker matched\n")
        self.assertEqual(
            (self.root / "check.stdout").read_text(encoding="utf-8"), "marker matched\n"
        )

    def test_nested_artifacts_create_their_directories(self):
        self.exporter.write_text("logs/runner.log", "line\n")
        self.assertTrue((self.root / "logs" / "runner.log").is_file())

    def test_a_confirmed_export_describes_every_artifact(self):
        self.exporter.write_text("check.stdout", "marker matched\n")
        self.exporter.write_json("run.json", {"status": "SETTLED"})
        record = self.exporter.confirm()
        self.assertEqual(record.status, status.ExportStatus.CONFIRMED)
        paths = {entry["path"] for entry in record.artifacts}
        self.assertEqual(paths, {"check.stdout", "run.json"})
        for entry in record.artifacts:
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertGreater(entry["size_bytes"], 0)

    def test_receipt_fails_when_artifact_changes_after_write(self):
        self.exporter.write_text("check.stdout", "marker matched\n")
        (self.root / "check.stdout").write_text("", encoding="utf-8")
        record = self.exporter.confirm()
        self.assertNotEqual(record.status, status.ExportStatus.CONFIRMED)
        self.assertTrue(any("check.stdout" in entry for entry in record.missing))

    def test_receipt_fails_when_a_required_artifact_disappears(self):
        self.exporter.write_text("check.stdout", "marker matched\n")
        (self.root / "check.stdout").unlink()
        record = self.exporter.confirm()
        self.assertNotEqual(record.status, status.ExportStatus.CONFIRMED)
        self.assertTrue(any("check.stdout" in entry for entry in record.missing))

    def test_an_export_with_some_evidence_left_is_partial_not_failed(self):
        self.exporter.write_text("check.stdout", "marker matched\n")
        self.exporter.write_text("run.json", "{}\n")
        (self.root / "check.stdout").unlink()
        record = self.exporter.confirm()
        self.assertEqual(record.status, status.ExportStatus.PARTIAL)

    def test_an_export_with_nothing_left_has_failed(self):
        self.exporter.write_text("check.stdout", "marker matched\n")
        (self.root / "check.stdout").unlink()
        record = self.exporter.confirm()
        self.assertEqual(record.status, status.ExportStatus.FAILED)

    def test_an_optional_artifact_that_is_absent_does_not_block(self):
        self.exporter.write_text("run.json", "{}\n")
        self.exporter.declare("task-artifact/evidence.txt", required=False)
        record = self.exporter.confirm()
        self.assertEqual(record.status, status.ExportStatus.CONFIRMED)

    def test_a_required_artifact_declared_but_never_written_blocks(self):
        self.exporter.write_text("run.json", "{}\n")
        self.exporter.declare("diff.patch", required=True)
        record = self.exporter.confirm()
        self.assertNotEqual(record.status, status.ExportStatus.CONFIRMED)
        self.assertTrue(any("diff.patch" in entry for entry in record.missing))

    def test_only_a_confirmed_export_permits_cleanup(self):
        self.exporter.write_text("run.json", "{}\n")
        self.assertTrue(self.exporter.confirm().status.permits_cleanup)

    def test_the_receipt_is_written_and_reloads(self):
        self.exporter.write_text("run.json", "{}\n")
        record = self.exporter.confirm()
        receipt = self.root / "export-receipt.json"
        self.assertTrue(receipt.is_file())
        self.assertEqual(record.receipt_path, "export-receipt.json")
        reloaded = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["status"], "CONFIRMED")
        self.assertIn("artifacts", reloaded)

    def test_written_text_is_redacted_on_the_way_out(self):
        secret = "sk-ant-api-zzqwertyuiopasdfghjklzxcvbnmqwertyuiopasdfgh"
        self.exporter.write_text("logs/runner.log", f"key {secret}\n")
        written = (self.root / "logs" / "runner.log").read_text(encoding="utf-8")
        self.assertNotIn(secret, written)

    def test_written_structures_are_redacted_on_the_way_out(self):
        self.exporter.write_json("auth-receipt.json", {"token": "hunterhunterhunter"})
        written = (self.root / "auth-receipt.json").read_text(encoding="utf-8")
        self.assertNotIn("hunterhunterhunter", written)

    def test_a_path_escaping_the_run_directory_is_refused(self):
        with self.assertRaises(export.ExportError):
            self.exporter.write_text("../escape.txt", "x")

    def test_an_absolute_path_is_refused(self):
        with self.assertRaises(export.ExportError):
            self.exporter.write_text("/etc/passwd", "x")

    def test_an_adopted_tree_is_copied_and_described(self):
        source = Path(self._tmp.name) / "source"
        (source / "nested").mkdir(parents=True)
        (source / "nested" / "evidence.txt").write_text("marker\n", encoding="utf-8")
        self.exporter.adopt_tree("task-artifact", source, required=True)
        record = self.exporter.confirm()
        self.assertEqual(record.status, status.ExportStatus.CONFIRMED)
        self.assertTrue(
            (self.root / "task-artifact" / "nested" / "evidence.txt").is_file()
        )
        self.assertTrue(
            any(entry["path"].startswith("task-artifact/") for entry in record.artifacts)
        )

    def test_adopting_an_absent_tree_is_reported_rather_than_raised(self):
        self.exporter.write_text("run.json", "{}\n")
        self.exporter.adopt_tree("task-artifact", Path("/nonexistent"), required=True)
        record = self.exporter.confirm()
        self.assertNotEqual(record.status, status.ExportStatus.CONFIRMED)


class BoundedStreamTest(unittest.TestCase):
    """A captured stream is redacted first and bounded second, and says so."""

    LIMIT = 64

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "run"
        self.exporter = export.Exporter(self.root)
        self.addCleanup(self._tmp.cleanup)

    def written(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def test_a_stream_that_fits_is_kept_whole_and_unannotated(self):
        raw = export.bounded_stream("installed the package\n", limit=self.LIMIT)
        self.assertEqual(raw.decode("utf-8"), "installed the package\n")
        self.assertNotIn("bounded:", raw.decode("utf-8"))

    def test_a_stream_that_does_not_fit_keeps_its_end(self):
        """The end is where a command that failed says why."""
        text = "noise\n" * 200 + "could not install the package\n"
        raw = export.bounded_stream(text, limit=self.LIMIT).decode("utf-8")
        self.assertIn("could not install the package", raw)

    def test_a_bounded_stream_says_it_is_bounded_and_what_was_dropped(self):
        text = "x" * 500
        raw = export.bounded_stream(text, limit=self.LIMIT).decode("utf-8")
        first = raw.splitlines()[0]
        self.assertIn("bounded:", first)
        self.assertIn(str(500 - self.LIMIT), first)
        self.assertIn(str(self.LIMIT), first)

    def test_the_notice_and_the_kept_bytes_account_for_the_whole_stream(self):
        text = "y" * 1000
        raw = export.bounded_stream(text, limit=self.LIMIT)
        notice, _, kept = raw.partition(b"\n")
        self.assertEqual(len(kept), self.LIMIT)
        self.assertEqual(len(kept) + (1000 - self.LIMIT), len(text))
        self.assertIn(b"bounded:", notice)

    def test_a_forwarded_value_with_no_shape_is_removed(self):
        """Only the literal removes it, so the artifact has to be given it."""
        value = "opaquesessionmaterial"
        self.exporter.write_stream(
            "bootstrap/provision-01-curl.stdout",
            f"the helper answered {value} and exited\n",
            extra_values=(value,),
        )
        written = self.written("bootstrap/provision-01-curl.stdout")
        self.assertNotIn(value, written)
        self.assertIn(redact.VALUE_MARK, written)

    def test_a_credential_shape_is_removed_without_being_named(self):
        secret = "sk-ant-api-zzqwertyuiopasdfghjklzxcvbnmqwertyuiopasdfgh"
        self.exporter.write_stream(
            "bootstrap/provision-02-npm.stdout", f"config {secret}\n"
        )
        self.assertNotIn(secret, self.written("bootstrap/provision-02-npm.stdout"))

    def test_a_secret_lying_across_the_bound_is_not_kept_as_its_tail(self):
        """Redaction runs before the cut, or half a token survives as plain text.

        Half a shape matches no shape, so a bound taken first would keep the
        second half of this token as ordinary output and write it to the host.
        """
        secret = "sk-" + "q" * 16 + "unmistakabletail"
        text = "h" * 160 + "\n" + secret + "t" * 40
        self.exporter.write_stream(
            "bootstrap/provision-03-curl.stdout", text, limit=self.LIMIT
        )
        written = self.written("bootstrap/provision-03-curl.stdout")
        self.assertNotIn("unmistakabletail", written)
        self.assertIn(redact.TOKEN_MARK, written)

    def test_a_stream_artifact_is_required_so_losing_it_unconfirms_the_export(self):
        self.exporter.write_stream("bootstrap/provision-01-curl.stdout", "done\n")
        (self.root / "bootstrap" / "provision-01-curl.stdout").unlink()
        record = self.exporter.confirm()
        self.assertNotEqual(record.status, status.ExportStatus.CONFIRMED)
        self.assertTrue(
            any("provision-01-curl.stdout" in entry for entry in record.missing)
        )

    def test_an_empty_stream_is_still_an_artifact(self):
        """An empty stderr is how a reader tells a silent failure from a loud one."""
        self.exporter.write_stream("bootstrap/provision-01-curl.stderr", "")
        record = self.exporter.confirm()
        self.assertEqual(record.status, status.ExportStatus.CONFIRMED)
        self.assertIn(
            "bootstrap/provision-01-curl.stderr",
            {entry["path"] for entry in record.artifacts},
        )
