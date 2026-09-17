"""An identifier is not a picture, and a picture of the wrong screen is worse."""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from cycle_runner import display, screenshot, status

FRAME = b"\x89PNG\r\n\x1a\n a frame"
DIGEST = hashlib.sha256(FRAME).hexdigest()

CAPTURED = (
    "screen :99 captured\n"
    "file screen.png\n"
    f"bytes {len(FRAME)}\n"
    f"sha256 {DIGEST}\n"
)


class ArgvTest(unittest.TestCase):
    def test_the_screen_is_an_argument_rather_than_inherited(self):
        """A command that takes whichever DISPLAY it is handed takes theirs."""
        directory = "/home/cycle/screenshots/runtime-ready"
        argv = screenshot.capture_argv(":99", directory)
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertEqual(argv[3:], ["cycle-screenshot", ":99", directory])

    def test_the_capture_is_taken_from_the_x_server_not_from_the_application(self):
        self.assertIn("xwd", screenshot.CAPTURE_SCRIPT)
        self.assertIn('export DISPLAY="$1"', screenshot.CAPTURE_SCRIPT)


class ParseTest(unittest.TestCase):
    def test_a_capture_reports_its_file_its_size_and_its_digest(self):
        reported = screenshot.parse(CAPTURED)
        self.assertTrue(reported["captured"])
        self.assertEqual(reported["display"], ":99")
        self.assertEqual(reported["file"], "screen.png")
        self.assertEqual(reported["bytes"], len(FRAME))
        self.assertEqual(reported["sha256"], DIGEST)

    def test_a_screen_that_did_not_answer_reports_why_and_no_image(self):
        reported = screenshot.parse(
            "screen :99 not captured\nreason the screen did not answer\n"
        )
        self.assertFalse(reported["captured"])
        self.assertEqual(reported["reason"], "the screen did not answer")
        self.assertEqual(reported["file"], "")

    def test_nothing_at_all_is_not_a_capture(self):
        self.assertFalse(screenshot.parse("")["captured"])


class PermittedTest(unittest.TestCase):
    """The operator's screen is never photographed, whatever else is true."""

    def permitted(self, **overrides):
        options = {
            "source": screenshot.X_SERVER,
            "mode": display.VIRTUAL,
            "display": ":99",
            "operator_display": ":0",
        }
        options.update(overrides)
        return screenshot.permitted(**options)

    def test_the_screen_this_run_created_may_be_captured(self):
        allowed, why = self.permitted()
        self.assertTrue(allowed, why)

    def test_the_screen_the_operators_session_is_on_may_not(self):
        allowed, why = self.permitted(display=":0")
        self.assertFalse(allowed)
        self.assertIn("operator", why)

    def test_a_run_configured_onto_the_operators_screen_may_not_either(self):
        """Accepting a window on their desktop is not consent to photograph it."""
        allowed, why = self.permitted(mode=display.HOST, display=":0")
        self.assertFalse(allowed)
        self.assertIn("not part of it", why)

    def test_a_capture_with_no_screen_named_takes_whichever_one_it_inherits(self):
        allowed, why = self.permitted(display="")
        self.assertFalse(allowed)
        self.assertIn("inherits", why)

    def test_the_hypervisor_route_is_not_addressed_by_screen_number(self):
        """It names a domain, so the operator's screen is not reachable at all."""
        allowed, why = self.permitted(
            source=screenshot.HYPERVISOR, display=":0", operator_display=":0"
        )
        self.assertTrue(allowed, why)
        self.assertIn("framebuffer", why)


class JudgeTest(unittest.TestCase):
    """What is judged is the bytes the host holds, not the capture's own report."""

    def judge(self, **overrides):
        options = {
            "moment": screenshot.RUNTIME_READY,
            "source": screenshot.X_SERVER,
            "display": ":99",
            "artifact": "screenshots/runtime-ready.png",
            "data": FRAME,
            "reported": screenshot.parse(CAPTURED),
        }
        options.update(overrides)
        return screenshot.judge(**options)

    def test_an_image_on_the_host_is_a_picture_of_this_screen(self):
        capture = self.judge()
        self.assertTrue(capture.ok, capture.detail)
        self.assertEqual(capture.sha256, DIGEST)
        self.assertEqual(capture.size_bytes, len(FRAME))
        self.assertEqual(capture.artifact, "screenshots/runtime-ready.png")

    def test_an_image_that_never_reached_the_host_is_not_evidence(self):
        capture = self.judge(data=None)
        self.assertFalse(capture.ok)
        self.assertIn("nothing to look at", capture.detail)
        self.assertEqual(capture.artifact, "")

    def test_an_empty_file_is_not_an_image(self):
        capture = self.judge(data=b"")
        self.assertFalse(capture.ok)

    def test_an_image_that_is_not_the_one_that_was_taken_is_refused(self):
        capture = self.judge(data=b"some other bytes entirely")
        self.assertFalse(capture.ok)
        self.assertIn("not the one the capture reported taking", capture.detail)

    def test_a_capture_that_did_not_happen_keeps_the_reason_it_gave(self):
        reported = screenshot.parse(
            "screen :99 not captured\nreason the screen did not answer\n"
        )
        capture = self.judge(data=None, reported=reported)
        self.assertFalse(capture.ok)
        self.assertIn("the screen did not answer", capture.detail)

    def test_a_refusal_is_recorded_rather_than_raised(self):
        capture = self.judge(failure="that screen is the operator's own")
        self.assertFalse(capture.ok)
        self.assertEqual(capture.detail, "that screen is the operator's own")

    def test_the_hypervisor_route_reports_no_digest_and_is_judged_on_the_bytes(self):
        capture = self.judge(source=screenshot.HYPERVISOR, reported={})
        self.assertTrue(capture.ok, capture.detail)
        self.assertEqual(capture.sha256, DIGEST)
        self.assertIn("hypervisor", capture.detail)


class ScriptTest(unittest.TestCase):
    """The script and the parser have to agree, so the script really runs here.

    The X tools are stood in for rather than installed: what is under test is
    the script's control flow and the words it prints, not xwd. The screen it is
    pointed at is one no host has, so a stub that went missing would fail to
    connect rather than photograph whoever is running this.
    """

    ABSENT_SCREEN = ":77"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.target = self.root / "runtime-ready"
        self.addCleanup(self._tmp.cleanup)
        self.stub("xset", "exit 0")
        self.stub(
            "xwd",
            'out=""\n'
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in -out) out="$2"; shift;; esac\n'
            '  shift\n'
            'done\n'
            'printf "fake x window dump" > "$out"\n',
        )
        # The real magic number is a capital letter followed by a digit, and a
        # repository gate forbids writing one; the stub only has to pass bytes
        # along, so it passes different ones.
        self.stub("xwdtopnm", 'printf "fake pixmap"')
        self.stub("pnmtopng", 'cat > /dev/null; printf "\\211PNG fake"')

    def stub(self, name, body):
        path = self.tools / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def capture(self, directory=None):
        directory = self.target if directory is None else directory
        argv = screenshot.capture_argv(self.ABSENT_SCREEN, str(directory))
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env={"PATH": f"{self.tools}:/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return screenshot.parse(completed.stdout)

    def test_what_the_script_prints_is_what_the_parser_reads(self):
        reported = self.capture()
        self.assertTrue(reported["captured"], reported)
        self.assertEqual(reported["display"], self.ABSENT_SCREEN)
        self.assertEqual(reported["file"], "screen.png")
        landed = self.target / "screen.png"
        self.assertEqual(reported["bytes"], landed.stat().st_size)
        self.assertEqual(
            reported["sha256"], hashlib.sha256(landed.read_bytes()).hexdigest()
        )

    def test_a_screen_that_does_not_answer_writes_nothing_and_says_so(self):
        self.stub("xset", "exit 1")
        reported = self.capture()
        self.assertFalse(reported["captured"])
        self.assertEqual(reported["reason"], "the screen did not answer")
        self.assertFalse(self.target.exists())

    def test_a_dump_nothing_can_convert_is_kept_as_a_dump(self):
        """An image in an awkward format still beats no image at all."""
        self.stub("pnmtopng", "exit 1")
        reported = self.capture()
        self.assertTrue(reported["captured"], reported)
        self.assertEqual(reported["file"], "screen.xwd")
        self.assertFalse((self.target / "screen.png").exists())

    def test_a_capture_that_could_not_read_the_screen_is_not_one(self):
        self.stub("xwd", "exit 1")
        reported = self.capture()
        self.assertFalse(reported["captured"])
        self.assertIn("root window", reported["reason"])

    def test_no_directory_named_means_nothing_is_written_anywhere(self):
        reported = self.capture(directory="")
        self.assertFalse(reported["captured"])
        self.assertIn("no directory", reported["reason"])


class RecordTest(unittest.TestCase):
    def capture(self, moment, ok=True):
        return screenshot.judge(
            moment=moment,
            source=screenshot.X_SERVER,
            display=":99",
            artifact=f"screenshots/{moment}.png",
            data=FRAME if ok else None,
        )

    def test_a_run_that_took_no_picture_says_so(self):
        record = screenshot.record(":99", [])
        self.assertEqual(record.status, status.PhaseStatus.SKIPPED)
        self.assertEqual(record.captures, [])

    def test_both_moments_are_kept_in_the_order_they_were_taken(self):
        record = screenshot.record(
            ":99",
            [self.capture(screenshot.RUNTIME_READY), self.capture(screenshot.AFTER_REVIEW)],
        )
        self.assertEqual(record.status, status.PhaseStatus.OK)
        self.assertEqual(
            [entry["moment"] for entry in record.captures],
            ["runtime-ready", "after-review"],
        )
        self.assertIn("outside the application", record.detail)

    def test_a_capture_that_failed_is_not_reported_as_a_picture(self):
        record = screenshot.record(
            ":99",
            [
                self.capture(screenshot.RUNTIME_READY),
                self.capture(screenshot.AFTER_REVIEW, ok=False),
            ],
        )
        self.assertEqual(record.status, status.PhaseStatus.FAILED)
        self.assertIn("1 of 2", record.detail)
        self.assertIn("after-review", record.detail)

    def test_the_record_says_nothing_rests_on_a_picture(self):
        record = screenshot.record(":99", [self.capture("after-review", ok=False)])
        self.assertIn("nothing this run claims rests on", record.detail)


if __name__ == "__main__":
    unittest.main()
