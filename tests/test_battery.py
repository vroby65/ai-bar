import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_bar.app import AiBarWindow, BatteryState, battery_icon_name, read_battery_state


class BatteryTests(unittest.TestCase):
    def test_missing_battery_is_not_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_battery_state(Path(directory)))
            self.assertIsNone(read_battery_state(Path(directory) / "missing"))

    def test_reads_battery_and_ignores_ac_supply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, kind in (("AC", "Mains"), ("BAT0", "Battery")):
                supply = root / name
                supply.mkdir()
                (supply / "type").write_text(kind)
            (root / "BAT0/capacity").write_text("73\n")
            (root / "BAT0/status").write_text("Discharging\n")
            self.assertEqual(read_battery_state(root), BatteryState(73, "Discharging"))

    def test_icons_distinguish_charging_and_full(self):
        self.assertEqual(battery_icon_name(BatteryState(73, "Discharging")),
                         "battery-level-70-symbolic")
        self.assertEqual(battery_icon_name(BatteryState(73, "Charging")),
                         "battery-level-70-charging-symbolic")
        self.assertEqual(battery_icon_name(BatteryState(100, "Full")),
                         "battery-level-100-charged-symbolic")

    @patch("ai_bar.app.read_battery_state")
    def test_widget_updates_and_hides_when_battery_is_absent(self, read_state):
        window = AiBarWindow.__new__(AiBarWindow)
        window.battery_statuses = []
        window.status_labels = []
        window.volume_controls = []
        button = window._build_battery_status()
        try:
            read_state.return_value = BatteryState(73, "Charging")
            window._update_status_items()
            self.assertTrue(button.get_visible())
            self.assertEqual(window.battery_statuses[0][1].get_text(), "73%")
            self.assertIn("in carica", button.get_tooltip_text())
            read_state.return_value = None
            window._update_status_items()
            self.assertFalse(button.get_visible())
        finally:
            button.destroy()
