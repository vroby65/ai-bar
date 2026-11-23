import subprocess
import unittest
from unittest.mock import Mock, patch

from ai_bar.ssh_server import SSHServerTray, read_ssh_state, set_ssh_active


class SSHServerTests(unittest.TestCase):
    @patch("ai_bar.ssh_server.subprocess.run")
    def test_socket_activation_counts_as_running(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, (
            "Id=ssh.service\nLoadState=loaded\nActiveState=inactive\n\n"
            "Id=ssh.socket\nLoadState=loaded\nActiveState=active\n"
        ), "")
        self.assertEqual(read_ssh_state(), (True, ("ssh.service", "ssh.socket")))
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    @patch("ai_bar.ssh_server.subprocess.run")
    def test_service_without_socket_is_supported(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, (
            "Id=ssh.service\nLoadState=loaded\nActiveState=inactive\n\n"
            "Id=ssh.socket\nLoadState=not-found\nActiveState=inactive\n"
        ), "")
        self.assertEqual(read_ssh_state(), (False, ("ssh.service",)))

    @patch("ai_bar.ssh_server.subprocess.run")
    def test_missing_server_has_no_units(self, run):
        run.return_value = subprocess.CompletedProcess([], 1,
            "Id=ssh.service\nLoadState=not-found\nActiveState=inactive\n", "")
        self.assertEqual(read_ssh_state(), (False, ()))

    @patch("ai_bar.ssh_server.subprocess.run")
    def test_systemd_failure_is_reported(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "Bus unavailable")
        with self.assertRaisesRegex(RuntimeError, "Bus unavailable"):
            read_ssh_state()

    @patch("ai_bar.ssh_server.subprocess.run")
    def test_start_and_stop_use_authentication_and_stop_socket_too(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        set_ssh_active(True, ("ssh.service", "ssh.socket"))
        self.assertEqual(run.call_args.args[0], ["pkexec", "systemctl", "start", "ssh.service"])
        set_ssh_active(False, ("ssh.service", "ssh.socket"))
        self.assertEqual(run.call_args.args[0],
            ["pkexec", "systemctl", "stop", "ssh.service", "ssh.socket"])

    @patch("ai_bar.ssh_server.subprocess.run")
    def test_authentication_failure_is_reported(self, run):
        run.return_value = subprocess.CompletedProcess([], 126, "", "Not authorized")
        with self.assertRaisesRegex(RuntimeError, "Not authorized"):
            set_ssh_active(True, ("ssh.service",))


class SSHServerTrayTests(unittest.TestCase):
    def setUp(self):
        worker = patch("ai_bar.ssh_server.threading.Thread").start()
        timer = patch("ai_bar.ssh_server.GLib.timeout_add_seconds", return_value=123).start()
        patch("ai_bar.ssh_server.GLib.source_remove").start()
        self.addCleanup(patch.stopall)
        self.worker = worker
        self.timer = timer
        self.show_error = Mock()
        self.tray = SSHServerTray(self.show_error, 16)
        self.addCleanup(self.tray.close)

    def test_menu_stops_active_ssh_and_blocks_duplicate_actions(self):
        self.tray._finish((True, ("ssh.service", "ssh.socket")), None)
        self.assertIs(self.tray.icon.get_pixbuf(), self.tray.icons["green"])
        self.assertEqual(self.tray.action.get_label(), "Disattiva")
        self.tray.action.emit("activate")
        self.assertFalse(self.tray.action.get_sensitive())
        self.assertIs(self.worker.call_args.kwargs["args"][0], False)
        count = self.worker.call_count
        self.tray.action.emit("activate")
        self.tray._refresh()
        self.assertEqual(self.worker.call_count, count)

    def test_inactive_ssh_has_gray_icon_and_can_be_started(self):
        self.tray._finish((False, ("ssh.service",)), None)
        self.assertIs(self.tray.icon.get_pixbuf(), self.tray.icons["gray"])
        self.assertTrue(self.tray.action.get_sensitive())
        self.assertEqual(self.tray.action.get_label(), "Attiva")
        self.tray.action.emit("activate")
        self.assertIs(self.worker.call_args.kwargs["args"][0], True)

    def test_missing_service_is_gray_and_action_is_disabled(self):
        self.tray._finish((False, ()), None)
        self.assertIs(self.tray.icon.get_pixbuf(), self.tray.icons["gray"])
        self.assertFalse(self.tray.action.get_sensitive())
        self.assertIn("openssh-server", self.tray.icon.get_tooltip_text())

    def test_both_pointer_actions_open_the_menu(self):
        with patch.object(self.tray.menu, "popup") as popup:
            self.tray.icon.emit("activate")
            self.assertEqual(popup.call_args.args[-2], 0)
            self.tray.icon.emit("popup-menu", 3, 123)
            self.assertEqual(popup.call_args.args[-2:], (3, 123))

    @patch("ai_bar.ssh_server.GLib.idle_add")
    @patch("ai_bar.ssh_server.read_ssh_state", return_value=(True, ("ssh.service",)))
    @patch("ai_bar.ssh_server.set_ssh_active", side_effect=RuntimeError("Denied"))
    def test_failed_action_refreshes_real_state_and_reports_error(self, change, read, idle):
        self.tray._work(False)
        self.assertEqual(idle.call_args.args[1:], ((True, ("ssh.service",)), "Denied"))
        self.tray._finish(*idle.call_args.args[1:])
        self.assertTrue(self.tray.action.get_sensitive())
        self.show_error.assert_called_once_with("Server SSH: Denied")

    @patch("ai_bar.ssh_server.GLib.idle_add")
    @patch("ai_bar.ssh_server.read_ssh_state", return_value=(False, ("ssh.service",)))
    @patch("ai_bar.ssh_server.set_ssh_active")
    def test_successful_action_refreshes_state(self, change, read, idle):
        self.tray._units = ("ssh.service",)
        self.tray._work(False)
        change.assert_called_once_with(False, ("ssh.service",))
        self.tray._finish(*idle.call_args.args[1:])
        self.assertEqual(self.tray.action.get_label(), "Attiva")
        self.show_error.assert_not_called()

    @patch("ai_bar.ssh_server.GLib.idle_add")
    @patch("ai_bar.ssh_server.read_ssh_state", side_effect=subprocess.TimeoutExpired("systemctl", 5))
    def test_unavailable_status_disables_button_without_repeated_dialogs(self, read, idle):
        self.tray._work(None)
        self.tray._finish(*idle.call_args.args[1:])
        self.assertFalse(self.tray.action.get_sensitive())
        self.assertIn("non disponibile", self.tray.icon.get_tooltip_text())
        self.show_error.assert_not_called()

    @patch("ai_bar.ssh_server.GLib.source_remove")
    def test_destroy_stops_polling_and_ignores_late_results(self, remove):
        self.tray.close()
        remove.assert_called_once_with(123)
        self.assertFalse(self.tray._refresh())
        self.tray._finish((True, ("ssh.service",)), "Late error")
        self.show_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
