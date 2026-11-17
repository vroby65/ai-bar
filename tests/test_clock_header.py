import unittest
from unittest.mock import Mock, patch

from ai_bar.app import AiBarWindow, Gdk, Gtk
from ai_bar.config import default_config


class ClockHeaderTests(unittest.TestCase):
    def setUp(self):
        thread_patch = patch("ai_bar.app.threading.Thread")
        self.thread = thread_patch.start()
        self.addCleanup(thread_patch.stop)
        remove_patch = patch("ai_bar.app.GLib.source_remove")
        remove_patch.start()
        self.addCleanup(remove_patch.stop)


    @patch("ai_bar.app.GLib.timeout_add_seconds", return_value=42)
    @patch("ai_bar.app.GLib.source_remove")
    def test_time_beside_stacked_date_and_ip_and_stops_on_destroy(self, remove, timeout):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = default_config()
        clock = window._build_clock()
        self.assertEqual(window.clock_details.get_children(),
                         [window.date_label, window.network_row])
        self.assertEqual(window.clock_box.get_children()[1:],
                         [window.time_label, window.clock_details])
        self.assertEqual(window.clock_box.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertEqual(window.clock_details.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertEqual(window.public_ip_label.get_xalign(), 0)
        self.assertEqual(window.public_ip_label.get_text(), "IP: —")
        timeout.assert_any_call(60, window._refresh_public_ip)
        clock.destroy()
        self.assertEqual(remove.call_args_list, [unittest.mock.call(42), unittest.mock.call(42)])
        self.assertFalse(window._show_public_ip("203.0.113.7", 125))
        self.assertEqual(window.public_ip_label.get_text(), "IP: —")


    @patch("ai_bar.app.GLib.timeout_add_seconds")
    @patch("ai_bar.ssh_server.SSHServerButton._start_work")
    def test_header_is_left_aligned_and_ssh_is_beside_ip(self, start_work, _timeout):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = default_config()
        clock = window._build_clock()
        self.addCleanup(clock.destroy)
        self.assertEqual(window.clock_box.get_halign(), Gtk.Align.START)
        self.assertEqual(window.time_label.get_xalign(), 0)
        ip, ssh = window.network_row.get_children()
        self.assertIs(ip.get_child(), window.public_ip_label)
        self.assertTrue(ssh.get_style_context().has_class("clock-ssh"))
        self.assertEqual(ssh.get_child().get_children()[1].get_text(), "SSH")
        ssh._units = ("ssh.service",)
        start_work.reset_mock()
        ssh.emit("clicked")
        start_work.assert_called_once_with(True)

    @patch("ai_bar.app.GLib.timeout_add_seconds")
    def test_clicking_public_ip_opens_librespeed_only_on_left_click(self, _timeout):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = default_config()
        window._launch = Mock()
        clock = window._build_clock()
        self.addCleanup(clock.destroy)
        for number in (2, 3, 1):
            event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
            event.button = number
            self.assertEqual(window.public_ip_link.emit("button-press-event", event), number == 1)
        window._launch.assert_called_once_with(["xdg-open", "https://librespeed.org/"])

    @patch("ai_bar.app.GLib.timeout_add_seconds")
    def test_panel_reload_is_beside_time_and_keeps_its_action(self, _timeout):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = default_config()
        window._reload = Mock()
        window._launch = Mock()
        clock = window._build_clock()
        self.addCleanup(clock.destroy)
        button, time_label, details = window.clock_box.get_children()
        self.assertEqual(button.get_tooltip_text(), "Reload")
        self.assertEqual(len(button.get_child().get_children()), 1)
        self.assertIs(time_label, window.time_label)
        self.assertIs(details, window.clock_details)
        self.assertEqual(button.get_valign(), Gtk.Align.CENTER)
        button.emit("clicked")
        window._reload.assert_called_once_with()
        window._launch.assert_not_called()


    @patch("ai_bar.app.GLib.timeout_add_seconds")
    @patch("ai_bar.app.GLib.idle_add")
    @patch("ai_bar.app.urllib.request.urlopen")
    def test_public_ip_fetch_runs_in_worker_and_updates_via_idle(self, urlopen, idle, _timeout):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = default_config()
        clock = window._build_clock()
        self.addCleanup(clock.destroy)
        fetch = self.thread.call_args.kwargs["target"]
        urlopen.assert_not_called()
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b"203.0.113.7\n"
        with patch("ai_bar.app.time.monotonic", side_effect=[10, 10.125]):
            fetch()
        urlopen.assert_called_once_with("https://api.ipify.org", timeout=5)
        idle.assert_called_once_with(window._show_public_ip, "203.0.113.7", 125)
        self.assertFalse(window._show_public_ip("203.0.113.7", 125))
        self.assertEqual(window.public_ip_label.get_text(), "IP: 203.0.113.7")
        for result in (b"invalid", OSError("offline")):
            with self.subTest(result=result):
                idle.reset_mock()
                if isinstance(result, Exception):
                    urlopen.side_effect = result
                else:
                    response.read.return_value = result
                fetch()
                idle.assert_called_once_with(window._show_public_ip, "—", None)


    @patch("ai_bar.app.GLib.timeout_add_seconds")
    def test_public_ip_color_tracks_latency_and_clears_previous_state(self, _timeout):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = default_config()
        clock = window._build_clock()
        self.addCleanup(clock.destroy)
        for latency, expected in ((50, "fast"), (200, "fast"),
                                  (201, "medium"), (800, "medium"),
                                  (801, "slow"), (None, "slow"), (100, "fast")):
            with self.subTest(latency=latency):
                window._show_public_ip("—" if latency is None else "203.0.113.7", latency)
                style = window.public_ip_label.get_style_context()
                for state in ("fast", "medium", "slow"):
                    self.assertEqual(style.has_class(f"connection-{state}"), state == expected)
                tooltip = window.public_ip_label.get_tooltip_text()
                self.assertIn("non disponibile" if latency is None else f"{latency:.0f} ms", tooltip)


    def test_bottom_session_row_excludes_reload(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {"session_buttons": [
            {"label": "Reload", "action": "reload"},
            {"label": "Logout", "command": ["openbox", "--exit"]},
            {"label": "Shutdown", "command": ["systemctl", "poweroff"]},
        ]}
        row = window._build_session_buttons()
        self.addCleanup(row.destroy)
        self.assertEqual([button.get_tooltip_text() for button in row.get_children()],
                         ["Logout", "Shutdown"])
