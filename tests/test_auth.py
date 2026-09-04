import os
import unittest
from unittest.mock import patch

from ai_bar import app


class AskpassTests(unittest.TestCase):
    @patch("ai_bar.app.os.access", return_value=True)
    def test_ai_bar_exposes_its_askpass_to_child_processes(self, _access):
        with patch.dict(os.environ, {}, clear=True):
            app.configure_askpass_environment()

            self.assertEqual(
                os.environ["SUDO_ASKPASS"],
                "/usr/local/bin/ai-bar-askpass",
            )

    @patch("ai_bar.app.os.access")
    def test_ai_bar_preserves_an_existing_askpass(self, access):
        with patch.dict(
            os.environ,
            {"SUDO_ASKPASS": "/custom/askpass"},
            clear=True,
        ):
            app.configure_askpass_environment()

            self.assertEqual(os.environ["SUDO_ASKPASS"], "/custom/askpass")
            access.assert_not_called()

    @patch("ai_bar.app.Gtk.main")
    @patch("ai_bar.app.AiBarWindow")
    @patch("ai_bar.app.load_config", return_value={})
    @patch("ai_bar.app.configure_askpass_environment")
    def test_main_configures_askpass_before_building_the_panel(
        self,
        configure_askpass,
        _load_config,
        _window,
        _gtk_main,
    ):
        self.assertEqual(app.main([]), 0)

        configure_askpass.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
