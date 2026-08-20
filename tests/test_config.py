import json
import tempfile
import unittest
from pathlib import Path

from ai_bar.config import ConfigError, default_config, load_config, merge_config, validate_config


class MonitorConfigTests(unittest.TestCase):
    def _config(self, **panel):
        config = default_config()
        config["panel"].update(panel)
        return config

    def test_monitor_keys_default_to_unset(self):
        config = default_config()

        self.assertIsNone(config["panel"]["monitor"])
        self.assertIsNone(config["panel"]["launch_monitor"])

    def test_monitor_accepts_an_index_or_a_connector_name(self):
        validate_config(self._config(monitor=1))
        validate_config(self._config(monitor="DP-1"))

    def test_launch_monitor_accepts_auto(self):
        validate_config(self._config(launch_monitor="auto"))

    def test_monitor_rejects_nonsense(self):
        for value in (-1, True, "", "   ", 1.5, []):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    validate_config(self._config(monitor=value))

    def test_launch_monitor_rejects_nonsense(self):
        for value in (-1, True, "", 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    validate_config(self._config(launch_monitor=value))


class ConfigTests(unittest.TestCase):
    def test_default_panel_matches_requested_shape(self):
        config = default_config()

        self.assertEqual(config["panel"]["width"], 400)
        self.assertEqual(config["panel"]["side"], "left")
        self.assertTrue(config["panel"]["resizable"])
        self.assertEqual(config["launcher_groups"][0]["buttons"][0]["label"], "Terminale")
        self.assertTrue(all(button["maximized"] for button in config["launcher_groups"][0]["buttons"]))
        self.assertEqual(
            [button["label"] for button in config["launcher_groups"][1]["buttons"]],
            ["Hermes", "Codex", "DS Code", "terminal"],
        )
        self.assertTrue(all(button["target"] == "terminal" for button in config["launcher_groups"][1]["buttons"]))
        self.assertEqual(config["terminal"]["command"], ["hermes"])
        self.assertEqual([item["type"] for item in config["tray"]["items"]], ["volume"])
        self.assertEqual(config["tray"]["items"][0]["command"], ["pavucontrol", "-t", "2"])
        self.assertEqual(
            [button["label"] for button in config["session_buttons"]],
            ["Reload", "Logout", "Reboot", "Powerdown"],
        )

    def test_user_config_overrides_nested_values_without_losing_defaults(self):
        merged = merge_config(
            default_config(),
            {
                "panel": {"side": "right"},
                "terminal": {"font": "Monospace 12"},
            },
        )

        self.assertEqual(merged["panel"]["side"], "right")
        self.assertEqual(merged["panel"]["width"], 400)
        self.assertEqual(merged["terminal"]["font"], "Monospace 12")

    def test_load_config_validates_side(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"panel": {"side": "top"}}), encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_config(path)

    def test_load_config_validates_session_button_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"session_buttons": [{"label": "Broken"}]}), encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_config(path)

    def test_load_config_allows_reload_action_without_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"session_buttons": [{"label": "Reload", "action": "reload"}]}), encoding="utf-8")

            config = load_config(path)

        self.assertEqual(config["session_buttons"][0]["action"], "reload")

    def test_load_config_validates_launcher_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "launcher_groups": [
                            {
                                "buttons": [
                                    {
                                        "label": "Broken",
                                        "command": ["broken"],
                                        "target": "external",
                                    }
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(path)

    def test_load_config_accepts_window_and_url_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "launcher_groups": [
                            {
                                "buttons": [
                                    {
                                        "label": "Caja",
                                        "command": ["caja"],
                                        "target": "window",
                                    },
                                    {
                                        "label": "Chat",
                                        "url": "https://example.com",
                                        "target": "url",
                                    },
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config["launcher_groups"][0]["buttons"][0]["target"], "window")
        self.assertEqual(config["launcher_groups"][0]["buttons"][1]["target"], "url")

    def test_load_config_requires_url_for_url_target(self):
        for buttons in (
            [{"label": "Chat", "target": "url"}],
            [{"label": "Chat", "url": "  ", "target": "url"}],
        ):
            with self.subTest(buttons=buttons):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "config.json"
                    path.write_text(
                        json.dumps({"launcher_groups": [{"buttons": buttons}]}),
                        encoding="utf-8",
                    )

                    with self.assertRaises(ConfigError):
                        load_config(path)

    def test_a_bad_webview_acceleration_policy_is_refused(self):
        config = default_config()
        config["webview"] = {"hardware_acceleration": "yes please"}

        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_the_three_webview_acceleration_policies_are_accepted(self):
        for value in ("never", "on-demand", "always"):
            with self.subTest(value=value):
                config = default_config()
                config["webview"] = {"hardware_acceleration": value}
                validate_config(config)

    def test_webview_acceleration_may_be_left_out(self):
        config = default_config()
        config.pop("webview", None)

        validate_config(config)


if __name__ == "__main__":
    unittest.main()
