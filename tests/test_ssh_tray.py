import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_bar.app import AiBarWindow, Gdk, Gio


class SSHTrayTests(unittest.TestCase):
    @patch("ai_bar.app.find_spec", return_value=object())
    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_starts_external_applet_and_closes_pipe_on_destroy(self, spawn, find_spec):
        window = SimpleNamespace(
            get_display=Gdk.Display.get_default,
            config={"tray": {"icon_size": 19}},
            ssh_tray=None,
            connect=Mock(),
        )

        AiBarWindow._start_ssh_tray(window)

        find_spec.assert_called_once_with("sshserver")
        spawn.assert_called_once_with(
            [sys.executable, "-m", "sshserver", "--icon-size", "19", "--watch-stdin"],
            Gio.SubprocessFlags.STDIN_PIPE,
        )
        self.assertIs(window.ssh_tray, spawn.return_value)
        signal, close = window.connect.call_args.args
        self.assertEqual(signal, "destroy")
        close(window)
        spawn.return_value.get_stdin_pipe.return_value.close.assert_called_once_with(None)

    @patch("ai_bar.app.find_spec", return_value=None)
    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_source_checkout_without_applet_can_start(self, spawn, _find_spec):
        window = SimpleNamespace(get_display=Gdk.Display.get_default, ssh_tray=None)
        AiBarWindow._start_ssh_tray(window)
        spawn.assert_not_called()
        self.assertIsNone(window.ssh_tray)

    @patch("ai_bar.app.find_spec")
    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_skips_applet_on_non_x11_display(self, spawn, find_spec):
        window = SimpleNamespace(get_display=lambda: object(), ssh_tray=None)
        AiBarWindow._start_ssh_tray(window)
        find_spec.assert_not_called()
        spawn.assert_not_called()
