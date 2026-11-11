import subprocess
import unittest
from unittest.mock import Mock, patch

from ai_bar.ssh_server import SSHServerButton, read_ssh_state, set_ssh_active


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


class SSHServerButtonTests(unittest.TestCase):
    def setUp(self):
        worker = patch("ai_bar.ssh_server.threading.Thread").start()
        timer = patch("ai_bar.ssh_server.GLib.timeout_add_seconds", return_value=123).start()
        patch("ai_bar.ssh_server.GLib.source_remove").start()
        self.addCleanup(patch.stopall)
        self.worker = worker
        self.timer = timer
        self.show_error = Mock()
        self.button = SSHServerButton(self.show_error)
        self.addCleanup(self.button.destroy)

    def test_click_uses_current_state_and_blocks_duplicate_actions(self):
        self.button._finish((True, ("ssh.service", "ssh.socket")), None)
        self.assertTrue(self.button.get_style_context().has_class("ssh-active"))
        self.assertIn("Disattiva", self.button.get_tooltip_text())
        self.button.emit("clicked")
        self.assertFalse(self.button.get_sensitive())
        self.assertFalse(self.worker.call_args.kwargs["args"][0])
        count = self.worker.call_count
        self.button.emit("clicked")
        self.button._refresh()
        self.assertEqual(self.worker.call_count, count)

    def test_off_and_missing_states(self):
        self.button._finish((False, ("ssh.service",)), None)
        self.assertTrue(self.button.get_sensitive())
        self.assertIn("Attiva", self.button.get_tooltip_text())
        self.assertFalse(self.button.get_style_context().has_class("ssh-active"))
        self.button._finish((False, ()), None)
        self.assertFalse(self.button.get_sensitive())
        self.assertIn("openssh-server", self.button.get_tooltip_text())

    @patch("ai_bar.ssh_server.GLib.idle_add")
    @patch("ai_bar.ssh_server.read_ssh_state", return_value=(True, ("ssh.service",)))
    @patch("ai_bar.ssh_server.set_ssh_active", side_effect=RuntimeError("Denied"))
    def test_failed_action_refreshes_real_state_and_reports_error(self, change, read, idle):
        self.button._work(False)
        self.assertEqual(idle.call_args.args[1:], ((True, ("ssh.service",)), "Denied"))
        self.button._finish(*idle.call_args.args[1:])
        self.assertTrue(self.button.get_sensitive())
        self.show_error.assert_called_once_with("Server SSH: Denied")

    @patch("ai_bar.ssh_server.GLib.idle_add")
    @patch("ai_bar.ssh_server.read_ssh_state", return_value=(False, ("ssh.service",)))
    @patch("ai_bar.ssh_server.set_ssh_active")
    def test_successful_action_refreshes_state(self, change, read, idle):
        self.button._units = ("ssh.service",)
        self.button._work(False)
        change.assert_called_once_with(False, ("ssh.service",))
        self.button._finish(*idle.call_args.args[1:])
        self.assertIn("Attiva", self.button.get_tooltip_text())
        self.show_error.assert_not_called()

    @patch("ai_bar.ssh_server.GLib.idle_add")
    @patch("ai_bar.ssh_server.read_ssh_state", side_effect=subprocess.TimeoutExpired("systemctl", 5))
    def test_unavailable_status_disables_button_without_repeated_dialogs(self, read, idle):
        self.button._work(None)
        self.button._finish(*idle.call_args.args[1:])
        self.assertFalse(self.button.get_sensitive())
        self.assertIn("non disponibile", self.button.get_tooltip_text())
        self.show_error.assert_not_called()

    @patch("ai_bar.ssh_server.GLib.source_remove")
    def test_destroy_stops_polling_and_ignores_late_results(self, remove):
        self.button.destroy()
        remove.assert_called_once_with(123)
        self.assertFalse(self.button._refresh())
        self.button._finish((True, ("ssh.service",)), "Late error")
        self.show_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
