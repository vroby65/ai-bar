import os
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ai_bar.app import (
    AiBarWindow,
    clean_window_title,
    clock_labels_fit_inline,
    find_window_by_xid,
    panel_x_for_state,
    set_system_muted,
    set_system_volume,
    terminal_argv,
    volume_icon_name,
    volume_state_from_pactl,
    volume_state_from_wpctl,
    volume_status_from_pactl,
    volume_status_from_wpctl,
)
from ai_bar.xembed_tray import TRAY_BACKGROUND_RGB, TRAY_COLOR_VALUES


class ClockLayoutTests(unittest.TestCase):
    @patch("ai_bar.app.XEmbedTrayHost")
    @patch("ai_bar.app.XAppStatusIconHost")
    def test_status_and_tray_flows_are_left_aligned(self, xapp_host, xembed_host):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {
            "panel": {"side": "left"},
            "tray": {"items": [], "icon_size": 24, "xembed": True},
        }
        window.status_labels = []
        window.volume_controls = []
        window.tray_host = None
        window.xapp_tray_host = None

        status_area = window._build_tray_row()
        status_flow, tray_flow, _window_flow = status_area.get_children()

        self.assertEqual(status_flow.get_direction(), Gtk.TextDirection.LTR)
        self.assertEqual(tray_flow.get_direction(), Gtk.TextDirection.LTR)
        xapp_host.assert_called_once_with(tray_flow, 24, "left")
        xembed_host.assert_called_once_with(tray_flow, 24)

        status_area.destroy()

    def test_clock_labels_fit_inline_when_width_allows_it(self):
        self.assertTrue(clock_labels_fit_inline(120, 50, 60, 10))
        self.assertFalse(clock_labels_fit_inline(119, 50, 60, 10))

    def test_terminal_argv_runs_commands_through_user_shell(self):
        with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(terminal_argv(["hermes"]), ["/bin/bash", "-lc", "hermes"])
            self.assertEqual(terminal_argv(["codex", "my project"]), ["/bin/bash", "-lc", "codex 'my project'"])
            self.assertEqual(terminal_argv(None), ["/bin/bash"])

    def test_panel_x_for_state_slides_off_screen(self):
        self.assertEqual(panel_x_for_state("left", 0, 1920, 400, False), 0)
        self.assertEqual(panel_x_for_state("left", 0, 1920, 400, True), -400)
        self.assertEqual(panel_x_for_state("right", 0, 1920, 400, False), 1520)
        self.assertEqual(panel_x_for_state("right", 0, 1920, 400, True), 1920)

    def test_clean_window_title_collapses_whitespace(self):
        self.assertEqual(clean_window_title("  Terminale   - fish  "), "Terminale - fish")
        self.assertEqual(clean_window_title("   "), "Finestra")

    def test_find_window_by_xid_uses_current_window_list(self):
        class Window:
            def __init__(self, xid):
                self.xid = xid

            def get_xid(self):
                return self.xid

        target = Window(20)
        self.assertIs(find_window_by_xid([Window(10), target], 20), target)
        self.assertIsNone(find_window_by_xid([Window(10)], 20))

    def test_tray_color_hint_uses_four_rgb_triplets(self):
        self.assertEqual(len(TRAY_BACKGROUND_RGB), 3)
        self.assertTrue(all(0 <= value <= 65535 for value in TRAY_BACKGROUND_RGB))
        self.assertLess(max(TRAY_BACKGROUND_RGB), 32768)
        self.assertEqual(len(TRAY_COLOR_VALUES), 12)
        self.assertTrue(all(0 <= value <= 65535 for value in TRAY_COLOR_VALUES))
        self.assertGreater(TRAY_COLOR_VALUES[0], 32768)

    def test_volume_status_from_wpctl(self):
        self.assertEqual(volume_status_from_wpctl("Volume: 0.40"), "40%")
        self.assertEqual(volume_status_from_wpctl("Volume: 1.23"), "123%")
        self.assertEqual(volume_status_from_wpctl("Volume: 0.40 [MUTED]"), "Mute")

    def test_volume_status_from_pactl_uses_loudest_channel(self):
        output = "Volume: front-left: 26291 /  40% / -23.80 dB, front-right: 80609 / 123% / 5.41 dB"
        self.assertEqual(volume_status_from_pactl(output), "123%")

    def test_volume_state_keeps_level_while_muted(self):
        wpctl_state = volume_state_from_wpctl("Volume: 0.40 [MUTED]")
        self.assertEqual((wpctl_state.percent, wpctl_state.muted), (40, True))

        pactl_state = volume_state_from_pactl("Volume: mono: 32768 / 50% / -18.00 dB", "Mute: yes")
        self.assertEqual((pactl_state.percent, pactl_state.muted), (50, True))

    def test_volume_icon_matches_level_and_mute(self):
        self.assertEqual(volume_icon_name(80, True), "audio-volume-muted-symbolic")
        self.assertEqual(volume_icon_name(0, False), "audio-volume-muted-symbolic")
        self.assertEqual(volume_icon_name(20, False), "audio-volume-low-symbolic")
        self.assertEqual(volume_icon_name(55, False), "audio-volume-medium-symbolic")
        self.assertEqual(volume_icon_name(80, False), "audio-volume-high-symbolic")

    @patch("ai_bar.app.run_system_command", side_effect=[False, True])
    def test_set_volume_clamps_and_falls_back_to_pactl(self, run_command):
        self.assertTrue(set_system_volume(120))
        self.assertEqual(
            [call.args[0] for call in run_command.call_args_list],
            [
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "100%"],
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"],
            ],
        )

    @patch("ai_bar.app.run_system_command", return_value=True)
    def test_set_muted_uses_wpctl(self, run_command):
        self.assertTrue(set_system_muted(True))
        run_command.assert_called_once_with(
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]
        )


if __name__ == "__main__":
    unittest.main()
