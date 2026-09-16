"""A virtual screen is not where the application went; it is where it could go."""

import unittest

from cycle_runner import display, isolate, processes

W = display.Window


def windows(*pairs):
    return [W(identifier=str(i), name=n) for i, n in pairs]


class ParseTest(unittest.TestCase):
    def test_a_reachable_screen_and_its_windows_are_read(self):
        reachable, found = display.parse(
            "display :99 reachable\nwindow 1000 openbox\nwindow 2000 orca — project\n"
        )
        self.assertTrue(reachable)
        self.assertEqual([w.name for w in found], ["openbox", "orca — project"])

    def test_an_unreachable_screen_reports_no_windows(self):
        reachable, found = display.parse("display :99 unreachable\n")
        self.assertFalse(reachable)
        self.assertEqual(found, [])

    def test_a_window_with_no_name_is_still_a_window(self):
        _, found = display.parse("display :99 reachable\nwindow 7\n")
        self.assertEqual([(w.identifier, w.name) for w in found], [("7", "")])


class VerdictTest(unittest.TestCase):
    def verdict(self, **overrides):
        options = {
            "mode": display.VIRTUAL,
            "reachable": True,
            "before": windows((1000, "openbox")),
            "after": windows((1000, "openbox"), (2000, "orca")),
            "application": "orca",
        }
        options.update(overrides)
        return display.verdict(**options)

    def test_a_window_that_appeared_on_this_screen_is_where_it_went(self):
        ok, why = self.verdict()
        self.assertTrue(ok, why)
        self.assertIn("appeared on the screen this run created", why)

    def test_nothing_new_and_nothing_named_does_not_establish_anything(self):
        ok, why = self.verdict(after=windows((1000, "openbox")))
        self.assertFalse(ok)
        self.assertIn("nothing here says where", why)

    def test_an_application_already_there_is_recognised_by_name(self):
        already = windows((1000, "openbox"), (2000, "orca"))
        ok, why = self.verdict(before=already, after=already)
        self.assertTrue(ok, why)
        self.assertIn("already there", why)

    def test_a_screen_that_did_not_answer_establishes_nothing(self):
        ok, why = self.verdict(reachable=False)
        self.assertFalse(ok)
        self.assertIn("did not answer", why)

    def test_a_process_pointing_at_the_operators_session_is_conclusive(self):
        leaking = [processes.Process(pid=42, command="orca-ide", session="WAYLAND_DISPLAY=")]
        ok, why = self.verdict(leaking=leaking)
        self.assertFalse(ok)
        self.assertIn("42", why)
        self.assertIn("re-introduced", why)

    def test_asking_for_the_operators_screen_is_not_a_failure(self):
        ok, why = self.verdict(mode=display.HOST, after=windows((1000, "openbox")))
        self.assertTrue(ok, why)
        self.assertIn("what was asked for", why)

    def test_the_finding_does_not_rest_on_an_empty_environment(self):
        """Electron rewrites its own environ, so absence proves nothing."""
        _, why = self.verdict()
        self.assertIn("rewrites its own environment block", why)


class CarryingHostSessionTest(unittest.TestCase):
    def test_a_process_with_a_session_variable_is_returned(self):
        found = display.carrying_host_session(
            [
                processes.Process(pid=1, command="a", session=""),
                processes.Process(pid=2, command="b", session="WAYLAND_DISPLAY="),
            ]
        )
        self.assertEqual([p.pid for p in found], [2])

    def test_no_processes_is_no_finding(self):
        self.assertEqual(display.carrying_host_session([]), [])


class StrippingTest(unittest.TestCase):
    """The variables that point at the operator's screen must not survive."""

    def test_every_session_variable_the_display_check_names_is_stripped(self):
        for name in display.HOST_SESSION_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, isolate.LEAKING_NAMES)

    def test_the_variable_that_chose_the_operators_compositor_is_stripped(self):
        self.assertIn("WAYLAND_DISPLAY", isolate.LEAKING_NAMES)

    def test_the_runtime_directory_holding_that_socket_is_stripped(self):
        self.assertIn("XDG_RUNTIME_DIR", isolate.LEAKING_NAMES)


if __name__ == "__main__":
    unittest.main()
