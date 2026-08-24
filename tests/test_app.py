import os
import signal
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, GdkPixbuf, Gtk, Vte

from ai_bar.app import (
    AiBarWindow,
    WindowInfo,
    X,
    X11SuperToggle,
    clean_window_title,
    clock_labels_fit_inline,
    configuration_assistant_command,
    find_window_by_xid,
    maximize_launched_window,
    centered_position,
    panel_animation_step,
    panel_vertical_span,
    place_window,
    panel_x_for_state,
    webkit_cookie_storage_path,
    launcher_page_key,
    favicon_cache_path,
    same_origin,
    set_system_muted,
    set_system_volume,
    terminal_argv,
    terminal_session_key,
    volume_icon_name,
    volume_state_from_pactl,
    volume_state_from_wpctl,
    volume_status_from_pactl,
    volume_status_from_wpctl,
)
from ai_bar.xembed_tray import TRAY_BACKGROUND_RGB, TRAY_COLOR_VALUES


class ClockLayoutTests(unittest.TestCase):
    @patch("ai_bar.app.GLib.timeout_add_seconds")
    @patch("ai_bar.app.XEmbedTrayHost")
    @patch("ai_bar.app.XAppStatusIconHost")
    def test_configuration_assistant_is_separate_and_before_volume(
        self,
        _xapp_host,
        _xembed_host,
        _timeout_add,
    ):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {
            "panel": {"side": "left"},
            "tray": {
                "items": [{"type": "volume"}],
                "icon_size": 24,
                "xembed": False,
                "status_refresh_seconds": 5,
            },
        }
        window.status_labels = []
        window.volume_controls = []
        window.tray_host = None
        window.xapp_tray_host = None
        window._open_configuration_assistant = Mock()

        status_area = window._build_tray_row()

        status_flow = status_area.get_children()[0]
        assistant_button, volume_control = [
            child.get_child() for child in status_flow.get_children()
        ]
        self.assertIsInstance(assistant_button, Gtk.Button)
        self.assertIsInstance(volume_control, Gtk.Box)
        self.assertNotIn(assistant_button, volume_control.get_children())
        self.assertEqual(assistant_button.get_tooltip_text(), "Configura AI-bar con un agente")
        assistant_button.emit("clicked")
        window._open_configuration_assistant.assert_called_once_with()
        status_area.destroy()

    def test_configuration_assistant_command_uses_active_config(self):
        self.assertEqual(
            configuration_assistant_command(Path("/tmp/ai bar.json")),
            [
                os.sys.executable,
                "-m",
                "ai_bar.config_assistant",
                "--config",
                "/tmp/ai bar.json",
            ],
        )

    def test_opening_configuration_assistant_resets_previous_session(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config_path = Path("/tmp/config.json")
        command = configuration_assistant_command(window.config_path)
        key = terminal_session_key(command)
        previous_terminal = Mock()
        window.terminals = {key: previous_terminal}
        window.detached = {}
        window.terminal_notebook = Mock()
        window.terminal_notebook.page_num.return_value = 2
        window._switch_terminal = Mock()

        window._open_configuration_assistant()

        self.assertNotIn(key, window.terminals)
        window.terminal_notebook.remove_page.assert_called_once_with(2)
        previous_terminal.destroy.assert_called_once_with()
        window._switch_terminal.assert_called_once_with(command, "Configura")

    def test_opening_configuration_assistant_resets_a_detached_session(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config_path = Path("/tmp/config.json")
        command = configuration_assistant_command(window.config_path)
        key = terminal_session_key(command)
        previous_terminal = Mock()
        detached_window = Mock()
        detached_window.get_child.return_value = previous_terminal
        window.terminals = {key: previous_terminal}
        window.detached = {key: detached_window}
        window.terminal_notebook = Mock()
        window.terminal_notebook.page_num.return_value = -1
        window._switch_terminal = Mock()

        window._open_configuration_assistant()

        self.assertNotIn(key, window.detached)
        detached_window.remove.assert_called_once_with(previous_terminal)
        detached_window.destroy.assert_called_once_with()
        previous_terminal.destroy.assert_called_once_with()
        window._switch_terminal.assert_called_once_with(command, "Configura")

    def test_window_button_uses_the_application_icon(self):
        window = AiBarWindow.__new__(AiBarWindow)
        icon = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 16, 16)

        button = window._build_window_button(WindowInfo(1, "Firefox", False, icon))

        image = button.get_child().get_children()[0]
        self.assertEqual(image.get_storage_type(), Gtk.ImageType.PIXBUF)
        self.assertIsNotNone(image.get_pixbuf())
        button.destroy()

    def test_window_button_falls_back_to_the_generic_icon(self):
        window = AiBarWindow.__new__(AiBarWindow)

        button = window._build_window_button(WindowInfo(1, "Finestra", False, None))

        image = button.get_child().get_children()[0]
        self.assertEqual(image.get_icon_name()[0], "window-symbolic")
        button.destroy()

    def test_maximized_launcher_requests_a_maximized_window(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._launch = Mock()
        button = window._build_launcher_button(
            {
                "label": "Firefox",
                "command": ["firefox"],
                "maximized": True,
            }
        )

        button.emit("clicked")

        window._launch.assert_called_once_with(["firefox"], maximized=True)
        button.destroy()

    def test_window_launcher_uses_embedded_window_switch(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._switch_embedded_window = Mock()
        window.launcher_buttons = {}
        button = window._build_launcher_button(
            {"label": "Caja", "command": ["caja"], "target": "window"}
        )

        button.emit("clicked")

        window._switch_embedded_window.assert_called_once_with(["caja"], "Caja")
        button.destroy()

    def test_url_launcher_uses_webview_switch(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._switch_webview = Mock()
        window.launcher_buttons = {}
        button = window._build_launcher_button(
            {"label": "Chat", "url": "https://example.com", "target": "url"}
        )

        button.emit("clicked")

        window._switch_webview.assert_called_once_with("https://example.com", "Chat")
        button.destroy()

    def test_legacy_reboot_button_requests_authorization(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._launch_session_action = Mock()
        button = window._build_session_button(
            {
                "label": "Reboot",
                "command": ["systemctl", "reboot"],
            }
        )

        button.emit("clicked")

        window._launch_session_action.assert_called_once_with("reboot")
        button.destroy()

    def test_legacy_powerdown_button_requests_authorization(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._launch_session_action = Mock()
        button = window._build_session_button(
            {
                "label": "Powerdown",
                "command": ["systemctl", "poweroff"],
            }
        )

        button.emit("clicked")

        window._launch_session_action.assert_called_once_with("poweroff")
        button.destroy()

    def test_session_action_reports_supervisor_failure(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._show_error = Mock()

        with tempfile.TemporaryDirectory() as temporary_directory:
            result_path = Path(temporary_directory) / "ai-bar-session-result-1234"

            def write_failure(_pid, _signal):
                result_path.write_text("1\nAccess denied\n", encoding="utf-8")

            with (
                patch.dict(
                    os.environ,
                    {
                        "AI_BAR_SESSION_SUPERVISOR_PID": "1234",
                        "AI_BAR_SESSION_RESULT": str(result_path),
                        "AI_BAR_SESSION_RESULT_DIR": temporary_directory,
                        "XDG_RUNTIME_DIR": temporary_directory,
                    },
                ),
                patch("ai_bar.app.os.getppid", return_value=1234),
                patch("ai_bar.app.os.kill", side_effect=write_failure) as kill,
                patch("ai_bar.app.GLib.idle_add", side_effect=lambda fn, *args: fn(*args)),
            ):
                window._run_session_action("reboot")

        kill.assert_called_once_with(1234, signal.SIGUSR1)
        window._show_error.assert_called_once_with(
            "Comando non riuscito (1): systemctl reboot\nAccess denied"
        )

    def test_failed_session_command_shows_its_error(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window._show_error = Mock()
        completed = SimpleNamespace(returncode=1, stdout="", stderr="Not authorized\n")

        with (
            patch("ai_bar.app.subprocess.run", return_value=completed),
            patch("ai_bar.app.GLib.idle_add", side_effect=lambda fn, *args: fn(*args)),
        ):
            window._run_session_command(["pkexec", "/usr/bin/systemctl", "reboot"])

        window._show_error.assert_called_once_with(
            "Comando non riuscito (1): pkexec /usr/bin/systemctl reboot\nNot authorized"
        )

    def test_webview_ctrl_plus_zooms_in(self):
        window = AiBarWindow.__new__(AiBarWindow)
        webview = Mock()
        webview.get_zoom_level.return_value = 1.0
        event = Mock(state=Gdk.ModifierType.CONTROL_MASK, keyval=Gdk.KEY_plus)

        self.assertTrue(window._on_webview_key_press(webview, event))
        webview.set_zoom_level.assert_called_once_with(1.1)

    def test_webview_ctrl_minus_zooms_out(self):
        window = AiBarWindow.__new__(AiBarWindow)
        webview = Mock()
        webview.get_zoom_level.return_value = 1.0
        event = Mock(state=Gdk.ModifierType.CONTROL_MASK, keyval=Gdk.KEY_minus)

        self.assertTrue(window._on_webview_key_press(webview, event))
        webview.set_zoom_level.assert_called_once_with(0.9)

    def test_launcher_group_uses_an_adaptive_flowbox(self):
        window = AiBarWindow.__new__(AiBarWindow)

        group = window._build_launcher_group(
            {
                "title": "",
                "buttons": [
                    {"label": "One", "command": ["one"]},
                    {"label": "Two", "command": ["two"]},
                    {"label": "Three", "command": ["three"]},
                ],
            }
        )

        flow = group.get_children()[0]
        self.assertIsInstance(flow, Gtk.FlowBox)
        self.assertTrue(flow.get_homogeneous())
        self.assertEqual(flow.get_min_children_per_line(), 1)
        self.assertEqual(flow.get_max_children_per_line(), 3)
        self.assertEqual(len(flow.get_children()), 3)
        for child in flow.get_children():
            self.assertTrue(child.get_child().get_hexpand())

        group.destroy()

    def test_webkit_cookie_storage_path_uses_xdg_data_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg-data"}):
            self.assertEqual(
                webkit_cookie_storage_path(),
                Path("/tmp/xdg-data/ai-bar/webkit/cookies.sqlite"),
            )

    @patch("ai_bar.app.WebKit2")
    def test_webview_persistence_uses_a_persistent_cookie_store(self, webkit2):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg-data"}):
            expected_path = Path("/tmp/xdg-data/ai-bar/webkit/cookies.sqlite")

            context = Mock()
            data_manager = Mock()
            cookie_manager = Mock()
            webkit2.WebContext.get_default.return_value = context
            context.get_website_data_manager.return_value = data_manager
            data_manager.get_cookie_manager.return_value = cookie_manager

            window = AiBarWindow.__new__(AiBarWindow)
            window.web_context = None
            window._configure_webkit_cookie_persistence()

            cookie_manager.set_persistent_storage.assert_called_once_with(
                str(expected_path),
                webkit2.CookiePersistentStorage.SQLITE,
            )
            self.assertIs(window.web_context, context)

    @patch("ai_bar.app.WebKit2")
    def test_url_launcher_reuses_the_shared_webkit_context(self, webkit2):
        webview = Mock()
        webkit2.WebView.return_value = webview
        context = Mock()

        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {}
        window.web_context = context
        window.embedded = {}
        window.detached = {}
        window.favicon_targets = {}
        window.terminal_notebook = Mock()
        window.present = Mock()
        window._switch_webview("https://example.com", "Chat")

        # La vista si costruisce per proprieta' e non con new_with_context,
        # perche' il gestore dei contenuti si puo' passare solo alla nascita.
        # Cio' che conta resta il contesto condiviso: e' quello che tiene i
        # cookie, e senza di lui ogni scheda ripartirebbe dal login.
        webkit2.WebView.assert_called_once()
        self.assertIs(webkit2.WebView.call_args.kwargs["web_context"], context)
        webview.load_uri.assert_called_once_with("https://example.com")
        window.terminal_notebook.append_page.assert_called_once()

    @patch("ai_bar.app.WebKit2")
    def test_url_launcher_renders_without_accelerated_compositing(self, webkit2):
        # Su driver dove l'allocazione del buffer GBM fallisce WebKit non
        # ripiega da solo: la scheda resta vuota invece di rendere in software.
        webview = Mock()
        settings = Mock()
        webview.get_settings.return_value = settings
        webkit2.WebView.return_value = webview

        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {}
        window.web_context = Mock()
        window.embedded = {}
        window.detached = {}
        window.terminal_notebook = Mock()
        window.present = Mock()
        window._switch_webview("https://example.com", "Chat")

        settings.set_hardware_acceleration_policy.assert_called_once_with(
            webkit2.HardwareAccelerationPolicy.NEVER
        )
        webview.set_settings.assert_called_once_with(settings)

    @patch("ai_bar.app.WebKit2")
    def test_the_acceleration_policy_can_be_configured(self, webkit2):
        # Chi ha una scheda che funziona rimette il comportamento originale di
        # WebKit senza toccare il codice.
        for wanted, expected in (
            ("never", "NEVER"),
            ("on-demand", "ON_DEMAND"),
            ("always", "ALWAYS"),
        ):
            with self.subTest(wanted=wanted):
                webview = Mock()
                settings = Mock()
                webview.get_settings.return_value = settings
                webkit2.WebView.return_value = webview

                window = AiBarWindow.__new__(AiBarWindow)
                window.config = {"webview": {"hardware_acceleration": wanted}}
                window.web_context = Mock()
                window.embedded = {}
                window.detached = {}
                window.terminal_notebook = Mock()
                window.present = Mock()
                window._switch_webview("https://example.com", "Chat")

                settings.set_hardware_acceleration_policy.assert_called_once_with(
                    getattr(webkit2.HardwareAccelerationPolicy, expected)
                )

    @patch("ai_bar.app.WebKit2")
    def test_an_unset_acceleration_policy_stays_off(self, webkit2):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {}

        self.assertIs(window._webview_acceleration_policy(),
                      webkit2.HardwareAccelerationPolicy.NEVER)

    def test_a_click_on_the_content_asks_for_keyboard_focus(self):
        # Il pannello e' un DOCK: senza richiesta esplicita i campi di testo
        # restano non editabili finche' non si cambia scheda.
        window = AiBarWindow.__new__(AiBarWindow)
        window.is_active = Mock(return_value=False)
        window.present = Mock()
        widget = Mock()

        handled = window._on_content_click(widget, Mock())

        window.present.assert_called_once()
        widget.grab_focus.assert_called_once()
        # False: il clic deve comunque arrivare al contenuto.
        self.assertFalse(handled)

    def test_a_click_on_the_content_leaves_an_active_panel_alone(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.is_active = Mock(return_value=True)
        window.present = Mock()
        widget = Mock()

        self.assertFalse(window._on_content_click(widget, Mock()))
        window.present.assert_not_called()
        widget.grab_focus.assert_not_called()

    def test_maximize_launched_window_ignores_windows_present_before_launch(self):
        class Window:
            def __init__(self, xid):
                self.xid = xid
                self.maximized = False

            def get_xid(self):
                return self.xid

            def maximize(self):
                self.maximized = True

        old_window = Window(10)
        new_window = Window(20)

        self.assertTrue(maximize_launched_window([old_window, new_window], {10}, None, None, 99))
        self.assertFalse(old_window.maximized)
        self.assertTrue(new_window.maximized)

    def test_maximize_launched_window_accepts_a_newly_activated_existing_window(self):
        class Window:
            def __init__(self, xid):
                self.xid = xid
                self.maximized = False

            def get_xid(self):
                return self.xid

            def maximize(self):
                self.maximized = True

        browser_window = Window(10)

        self.assertTrue(maximize_launched_window([browser_window], {10}, browser_window, 99, 99))
        self.assertTrue(browser_window.maximized)

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

    def test_status_button_icon_only_omits_the_label(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.status_labels = []

        button = window._build_status_button(
            {
                "type": "display",
                "label": "Display",
                "icon": "preferences-desktop-display-symbolic",
                "command": ["arandr"],
                "icon_only": True,
            }
        )

        children = button.get_child().get_children()
        self.assertEqual(len(children), 1)
        self.assertIsInstance(children[0], Gtk.Image)
        button.destroy()

    def test_status_button_without_icon_only_shows_icon_and_label(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.status_labels = []

        button = window._build_status_button(
            {
                "type": "display",
                "label": "Display",
                "icon": "preferences-desktop-display-symbolic",
                "command": ["arandr"],
            }
        )

        children = button.get_child().get_children()
        self.assertEqual(len(children), 2)
        self.assertIsInstance(children[0], Gtk.Image)
        self.assertIsInstance(children[1], Gtk.Label)
        button.destroy()

    def test_clock_labels_fit_inline_when_width_allows_it(self):
        self.assertTrue(clock_labels_fit_inline(120, 50, 60, 10))
        self.assertFalse(clock_labels_fit_inline(119, 50, 60, 10))

    def test_terminal_argv_runs_commands_through_user_shell(self):
        with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(terminal_argv(["hermes"]), ["/bin/bash", "-lc", "hermes"])
            self.assertEqual(terminal_argv(["codex", "my project"]), ["/bin/bash", "-lc", "codex 'my project'"])
            self.assertEqual(terminal_argv(None), ["/bin/bash"])

    def test_terminal_argv_passes_panel_width(self):
        with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(
                terminal_argv(["fish"], width_px=511),
                ["env", "AI_BAR_TERMINAL_WIDTH_PX=511", "/bin/bash", "-lc", "fish"],
            )

    def test_terminal_session_key_reuses_the_same_tool_session(self):
        self.assertEqual(terminal_session_key(["hermes"]), terminal_session_key(["hermes"]))
        self.assertNotEqual(terminal_session_key(["hermes"]), terminal_session_key(["codex"]))

    def test_switch_terminal_reuses_an_existing_session(self):
        window = AiBarWindow.__new__(AiBarWindow)
        hermes = Mock()
        window.terminals = {terminal_session_key(["hermes"]): hermes}
        window.detached = {}
        window.terminal_notebook = Mock()
        window.terminal_notebook.page_num.return_value = 2
        window._build_terminal = Mock()
        window.present = Mock()

        window._switch_terminal(["hermes"])

        window._build_terminal.assert_not_called()
        window.terminal_notebook.set_current_page.assert_called_once_with(2)
        hermes.grab_focus.assert_called_once_with()
        self.assertIs(window.terminal, hermes)

    def test_switch_terminal_keeps_other_tool_sessions_alive(self):
        window = AiBarWindow.__new__(AiBarWindow)
        hermes = Mock()
        codex = Mock()
        window.terminals = {terminal_session_key(["hermes"]): hermes}
        window.detached = {}
        window.terminal_notebook = Mock()
        window.terminal_notebook.append_page.return_value = 1
        window._build_terminal = Mock(return_value=codex)
        window.present = Mock()
        window.panel_width = 400

        window._switch_terminal(["codex"])

        self.assertIs(window.terminals[terminal_session_key(["hermes"])], hermes)
        self.assertIs(window.terminals[terminal_session_key(["codex"])], codex)
        hermes.destroy.assert_not_called()

    def test_new_terminal_is_shown_before_becoming_visible_child(self):
        window = AiBarWindow.__new__(AiBarWindow)
        terminal = Mock()
        calls = []
        terminal.show_all.side_effect = lambda: calls.append("show")
        window.terminals = {}
        window.detached = {}
        window.terminal_notebook = Mock()
        window.terminal_notebook.append_page.return_value = 1
        window.terminal_notebook.set_current_page.side_effect = lambda _page: calls.append("select")
        window._build_terminal = Mock(return_value=terminal)
        window.present = Mock()
        window.panel_width = 400

        window._switch_terminal(["codex"])

        self.assertEqual(calls, ["show", "select"])

    def test_switch_terminal_creates_a_named_tab_for_the_tool(self):
        window = AiBarWindow.__new__(AiBarWindow)
        terminal = Mock()
        window.terminals = {}
        window.detached = {}
        window.terminal_notebook = Mock()
        window.terminal_notebook.append_page.return_value = 0
        window._build_terminal = Mock(return_value=terminal)
        window.present = Mock()
        window.panel_width = 400

        window._switch_terminal(["ds-code"])

        tab_label = window.terminal_notebook.append_page.call_args.args[1]
        self.assertEqual(tab_label.get_text(), "DS Code")
        tab_label.destroy()

    def test_terminal_copy_and_paste_shortcuts(self):
        window = AiBarWindow.__new__(AiBarWindow)
        terminal = Mock()
        copy_event = Mock(state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, keyval=Gdk.KEY_c)
        paste_event = Mock(state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, keyval=Gdk.KEY_v)

        self.assertTrue(window._on_terminal_key_press(terminal, copy_event))
        self.assertTrue(window._on_terminal_key_press(terminal, paste_event))

        terminal.copy_clipboard_format.assert_called_once_with(Vte.Format.TEXT)
        terminal.paste_clipboard.assert_called_once_with()

    @patch("ai_bar.app.Vte.Terminal")
    def test_terminal_uses_readable_blue_palette(self, terminal_class):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {
            "terminal": {
                "command": None,
                "working_directory": "/tmp",
                "font": None,
                "scrollback_lines": 100,
            }
        }

        window._build_terminal()

        foreground, background, palette = terminal_class.return_value.set_colors.call_args.args
        self.assertEqual(foreground.to_string(), "rgb(242,242,238)")
        self.assertEqual(background.to_string(), "rgb(21,24,25)")
        self.assertEqual(palette[4].to_string(), "rgb(108,182,255)")
        self.assertEqual(palette[12].to_string(), "rgb(165,214,255)")

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

    def test_super_toggle_triggers_only_when_super_is_tapped_alone(self):
        callback = Mock()
        toggle = X11SuperToggle(callback)
        toggle.keycodes = {133}

        with patch("ai_bar.app.GLib.idle_add", side_effect=lambda fn: fn()):
            toggle._handle_event(Mock(type=X.KeyPress, detail=133))
            toggle._handle_event(Mock(type=X.KeyRelease, detail=133))

        callback.assert_called_once_with()

    def test_super_toggle_ignores_super_combinations(self):
        callback = Mock()
        toggle = X11SuperToggle(callback)
        toggle.keycodes = {133}

        with patch("ai_bar.app.GLib.idle_add", side_effect=lambda fn: fn()):
            toggle._handle_event(Mock(type=X.KeyPress, detail=133))
            toggle._handle_event(Mock(type=X.KeyPress, detail=23))
            toggle._handle_event(Mock(type=X.KeyRelease, detail=133))

        callback.assert_not_called()

    def test_super_toggle_parses_record_events_with_protocol_display(self):
        toggle = X11SuperToggle(Mock())
        toggle.display = Mock()
        toggle.display.display = object()
        event = Mock()
        parser = Mock()
        parser.parse_binary_value.return_value = (event, b"")
        reply = Mock(category=1, client_swapped=False, data=b"event")

        with (
            patch("ai_bar.app.record.FromServer", 1),
            patch("ai_bar.app.rq.EventField", return_value=parser),
            patch.object(toggle, "_handle_event") as handle_event,
        ):
            toggle._handle_record_reply(reply)

        parser.parse_binary_value.assert_called_once_with(
            b"event", toggle.display.display, None, None
        )
        handle_event.assert_called_once_with(event)

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

    def _panel_window(self, side):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {"panel": {"side": side, "height": "screen"}}
        window.panel_width = 300
        window.panel_hidden = False
        # 2560x1440 monitor with a 37 pixel panel at the bottom and a taskbar
        # on the right, so work area and geometry differ on both axes.
        window._monitor_geometry = lambda: SimpleNamespace(
            x=0, y=240, width=2560, height=1440)
        window._monitor_workarea = lambda: SimpleNamespace(
            x=0, y=240, width=2523, height=1403)
        window.set_default_size = Mock()
        window.resize = Mock()
        window.move = Mock()
        return window

    def test_panel_geometry_stops_at_the_work_area(self):
        window = self._panel_window("left")

        window._apply_panel_geometry()

        window.resize.assert_called_once_with(300, 1403)
        window.move.assert_called_once_with(0, 240)

    def test_panel_geometry_still_spans_the_full_monitor_width(self):
        # The horizontal placement must keep using the monitor geometry: the
        # work area width already excludes the strut the panel reserves, so
        # a right-hand panel would creep inwards on every reapply.
        window = self._panel_window("right")

        window._apply_panel_geometry()

        window.move.assert_called_once_with(2260, 240)

    def test_panel_content_scrolls_instead_of_growing_past_the_screen(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {
            "panel": {"side": "left", "resizable": False},
            "launcher_groups": [],
            "terminal": {},
        }
        window.terminals = {}
        window.embedded = {}
        window.launcher_buttons = {}
        window.detached = {}
        window.detach_button = None
        window.reload_button = None
        window._build_clock = lambda: Gtk.Label()
        window._build_tray_row = lambda: Gtk.Label()
        window._build_terminal = lambda _command: Gtk.Label()
        window._build_detach_bar = lambda: Gtk.Label()
        window._build_session_buttons = lambda: Gtk.Label()

        root = window._build_content()
        scroller = root.get_children()[0]

        self.assertIsInstance(scroller, Gtk.ScrolledWindow)
        self.assertEqual(
            scroller.get_policy(),
            (Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC),
        )
        root.destroy()

    def test_panel_span_stops_at_a_reserved_bottom_panel(self):
        # A desktop panel at the bottom shrinks the work area: 1440 pixels of
        # monitor, 37 reserved, so the panel gets 1403 and must not cover them.
        workarea = SimpleNamespace(x=0, y=240, width=2560, height=1403)

        self.assertEqual(panel_vertical_span(workarea, "screen"), (240, 1403))

    def test_panel_span_honours_an_explicit_height(self):
        workarea = SimpleNamespace(x=0, y=240, width=2560, height=1403)

        self.assertEqual(panel_vertical_span(workarea, 900), (240, 900))
        self.assertEqual(panel_vertical_span(workarea, "900"), (240, 900))

    def test_panel_span_uses_the_full_monitor_when_nothing_is_reserved(self):
        # With no dock or panel around, the work area equals the monitor, so
        # behaviour is unchanged for a bare window manager.
        monitor = SimpleNamespace(x=0, y=0, width=1920, height=1080)

        self.assertEqual(panel_vertical_span(monitor, "screen"), (0, 1080))

    def _display(self, *monitors):
        return SimpleNamespace(
            get_n_monitors=lambda: len(monitors),
            get_monitor=lambda index: monitors[index],
        )

    def test_apply_strut_clears_space_for_a_right_panel_not_on_the_screen_edge(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {"panel": {"side": "right", "reserve_space": True}}
        window.panel_hidden = False
        window.panel_width = 400
        window._monitor_geometry = lambda: SimpleNamespace(
            x=0, y=0, width=1920, height=1080
        )
        window._clear_strut = Mock()
        window._set_strut_values = Mock()
        left = self._display(
            SimpleNamespace(
                get_geometry=lambda: SimpleNamespace(x=0, y=0, width=1920, height=1080)
            ),
            SimpleNamespace(
                get_geometry=lambda: SimpleNamespace(x=1920, y=0, width=1080, height=1920)
            ),
        )

        with patch("ai_bar.app.Gdk.Display.get_default", return_value=left):
            window._apply_strut()

        window._clear_strut.assert_called_once()
        window._set_strut_values.assert_not_called()

    def test_apply_strut_keeps_space_for_a_right_panel_on_the_screen_edge(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {"panel": {"side": "right", "reserve_space": True}}
        window.panel_hidden = False
        window.panel_width = 400
        window._monitor_geometry = lambda: SimpleNamespace(
            x=1920, y=0, width=1080, height=1920
        )
        window._clear_strut = Mock()
        window._set_strut_values = Mock()
        display = self._display(
            SimpleNamespace(
                get_geometry=lambda: SimpleNamespace(x=0, y=0, width=1920, height=1080)
            ),
            SimpleNamespace(
                get_geometry=lambda: SimpleNamespace(x=1920, y=0, width=1080, height=1920)
            ),
        )

        with patch("ai_bar.app.Gdk.Display.get_default", return_value=display):
            window._apply_strut()

        window._clear_strut.assert_not_called()
        window._set_strut_values.assert_called_once_with(
            [0, 400, 0, 0, 0, 0, 0, 1919, 0, 0, 0, 0]
        )

    def _fake_window(self):
        class Window:
            def __init__(self):
                self.calls = []

            def get_geometry(self):
                return (0, 0, 800, 600)

            def unmaximize(self):
                self.calls.append("unmaximize")

            def set_geometry(self, gravity, mask, x, y, width, height):
                self.calls.append(("set_geometry", x, y))

            def maximize(self):
                self.calls.append("maximize")

        return Window()

    def _monitor(self, x, y, width, height):
        area = SimpleNamespace(x=x, y=y, width=width, height=height)
        return SimpleNamespace(get_workarea=lambda: area,
                               get_geometry=lambda: area)

    def _window_with_monitors(self, launch_monitor, panel_monitor=None):
        window = AiBarWindow.__new__(AiBarWindow)
        window.config = {"panel": {"monitor": panel_monitor,
                                   "launch_monitor": launch_monitor}}
        window.launch_monitor_warning_shown = False
        window.monitor_warning_shown = False
        primary = self._monitor(0, 240, 2560, 1403)
        secondary = self._monitor(2560, 0, 1050, 1680)
        window._primary_monitor = lambda: primary
        window._find_monitor = lambda wanted: {"DVI-D-0": primary,
                                               "DP-1": secondary}.get(wanted)
        window._resolve_monitor = lambda: (
            secondary if panel_monitor == "DP-1" else primary)
        return window

    def test_find_monitor_matches_connector_names_from_xrandr(self):
        window = AiBarWindow.__new__(AiBarWindow)
        primary = self._monitor(0, 240, 2560, 1403)
        secondary = self._monitor(2560, 0, 1050, 1680)
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_monitor=lambda index: (primary, secondary)[index],
        )
        xrandr_output = (
            "Screen 0: minimum 320 x 200, current 3610 x 1680, maximum 16384 x 16384\n"
            "DVI-D-0 connected primary 2560x1403+0+240 (normal left inverted right x axis y axis) 0mm x 0mm\n"
            "DP-1 connected 1050x1680+2560+0 (normal left inverted right x axis y axis) 0mm x 0mm\n"
        )

        with patch("ai_bar.app.Gdk.Display.get_default", return_value=display), patch(
            "ai_bar.app.run_text_command",
            return_value=xrandr_output,
        ):
            self.assertIs(window._find_monitor("DP-1"), secondary)

    def test_launch_area_is_off_by_default(self):
        # Unset means the window manager keeps deciding, so existing setups
        # are not silently changed by an upgrade.
        self.assertIsNone(self._window_with_monitors(None).\
                          _launch_area())

    def test_launch_area_auto_targets_the_primary_monitor(self):
        window = self._window_with_monitors("auto", panel_monitor="DP-1")

        area = window._launch_area()

        self.assertEqual((area.x, area.y), (0, 240))

    def test_launch_area_auto_does_nothing_when_the_panel_is_already_there(self):
        # Panel on the primary: moving windows to the primary would be pointless.
        window = self._window_with_monitors("auto")

        self.assertIsNone(window._launch_area())

    def test_launch_area_accepts_an_explicit_connector(self):
        window = self._window_with_monitors("DVI-D-0", panel_monitor="DP-1")

        area = window._launch_area()

        self.assertEqual((area.x, area.y), (0, 240))

    def test_launch_area_ignores_a_monitor_that_is_not_there(self):
        window = self._window_with_monitors("HDMI-9", panel_monitor="DP-1")

        self.assertIsNone(window._launch_area())

    def test_centered_position_centers_inside_the_area(self):
        area = SimpleNamespace(x=2560, y=0, width=1050, height=1680)

        self.assertEqual(centered_position(area, 800, 600), (2685, 540))

    def test_centered_position_keeps_oversized_windows_on_screen(self):
        area = SimpleNamespace(x=2560, y=0, width=1050, height=1680)

        self.assertEqual(centered_position(area, 2000, 2000), (2560, 0))

    def test_place_window_moves_before_maximizing(self):
        # Order matters: the window manager maximizes onto the monitor the
        # window sits on, so maximizing first would expand it on the wrong one.
        window = self._fake_window()

        with patch("ai_bar.app.Wnck", SimpleNamespace(
                WindowGravity=SimpleNamespace(CURRENT=0),
                WindowMoveResizeMask=SimpleNamespace(X=1, Y=2))):
            place_window(window, SimpleNamespace(x=0, y=240, width=2560, height=1403), True)

        self.assertEqual(window.calls,
                         ["unmaximize", ("set_geometry", 0, 240), "maximize"])

    def test_place_window_centers_when_not_maximizing(self):
        window = self._fake_window()

        with patch("ai_bar.app.Wnck", SimpleNamespace(
                WindowGravity=SimpleNamespace(CURRENT=0),
                WindowMoveResizeMask=SimpleNamespace(X=1, Y=2))):
            place_window(window, SimpleNamespace(x=0, y=240, width=2560, height=1403), False)

        self.assertNotIn("maximize", window.calls)
        self.assertIn(("set_geometry", 880, 641), window.calls)

    def test_place_window_without_an_area_only_maximizes(self):
        window = self._fake_window()

        place_window(window, None, True)

        self.assertEqual(window.calls, ["maximize"])

    def test_animation_step_never_overshoots_the_target(self):
        for distance in range(-40, 41):
            self.assertLessEqual(panel_animation_step(distance), abs(distance))

    def test_animation_reaches_the_target_for_every_panel_width(self):
        for width in range(120, 1400):
            for start, target in ((-width, 0), (0, -width)):
                position = start
                for _step in range(500):
                    distance = target - position
                    if abs(distance) <= 3:
                        break
                    step = panel_animation_step(distance)
                    position += step if distance > 0 else -step
                else:
                    self.fail(f"animation did not converge for panel width {width}")


class ActiveLauncherTests(unittest.TestCase):
    def test_page_key_matches_the_key_each_switch_registers(self):
        # Se queste due chiavi divergessero il bottone non si accenderebbe mai.
        self.assertEqual(
            launcher_page_key({"target": "terminal", "command": ["claude"]}),
            terminal_session_key(["claude"]))
        self.assertEqual(
            launcher_page_key({"target": "window", "command": ["caja"]}),
            "window:caja")
        self.assertEqual(
            launcher_page_key({"target": "url", "url": "https://example.com"}),
            "url:https://example.com")

    def test_a_plain_launcher_has_no_page_of_its_own(self):
        # Un bottone che lancia e basta non apre nessuna scheda.
        self.assertIsNone(launcher_page_key({"command": ["firefox"]}))

    def _window(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.terminals = {}
        window.detached = {}
        window.embedded = {}
        window.detached = {}
        window.launcher_buttons = {}
        return window

    def test_only_the_button_of_the_shown_page_stays_lit(self):
        window = self._window()
        claude, shell = Gtk.Button(), Gtk.Button()
        claude_page, shell_page = Gtk.Box(), Gtk.Box()
        window.terminals = {"claude": claude_page, "bash": shell_page}
        window.launcher_buttons = {"claude": claude, "bash": shell}

        window._highlight_launcher(claude_page)
        self.assertTrue(claude.get_style_context().has_class("active-launcher"))
        self.assertFalse(shell.get_style_context().has_class("active-launcher"))

        window._highlight_launcher(shell_page)
        self.assertFalse(claude.get_style_context().has_class("active-launcher"))
        self.assertTrue(shell.get_style_context().has_class("active-launcher"))

    def test_an_embedded_page_lights_its_button_too(self):
        window = self._window()
        chat = Gtk.Button()
        chat_page = Gtk.Box()
        window.embedded = {"url:https://example.com": chat_page}
        window.launcher_buttons = {"url:https://example.com": chat}

        window._highlight_launcher(chat_page)

        self.assertTrue(chat.get_style_context().has_class("active-launcher"))

    def test_a_page_without_a_button_leaves_them_all_dark(self):
        # La scheda iniziale del pannello non e' stata aperta da un bottone.
        window = self._window()
        claude = Gtk.Button()
        claude.get_style_context().add_class("active-launcher")
        window.terminals = {"claude": Gtk.Box()}
        window.launcher_buttons = {"claude": claude}

        window._highlight_launcher(Gtk.Box())

        self.assertFalse(claude.get_style_context().has_class("active-launcher"))


class DetachTests(unittest.TestCase):
    def _window(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.terminals = {}
        window.embedded = {}
        window.detached = {}
        window.launcher_buttons = {}
        window.detach_button = None
        window.reload_button = None
        window.config = {"quick_launchers": []}
        return window

    def test_the_first_tab_is_detachable_too(self):
        # Con terminal.command uguale al comando di un bottone, la prima scheda
        # e' anche la sua: escluderla renderebbe quel bottone inspiegabilmente
        # diverso dagli altri.
        window = self._window()

        self.assertTrue(window._detachable_page(Gtk.Box()))
        self.assertFalse(window._detachable_page(None))

    def test_page_actions_include_reload_for_the_current_tool(self):
        window = self._window()
        window._reload_current_page = Mock()

        bar = window._build_detach_bar()

        reload_button = next(
            child for child in bar.get_children()
            if child.get_tooltip_text() == "Ricarica il tool corrente"
        )
        reload_button.emit("clicked")

        window._reload_current_page.assert_called_once_with()
        bar.destroy()

    def test_page_actions_include_configured_quick_launchers(self):
        window = self._window()
        window.config["quick_launchers"] = [
            {"label": "AnyDesk", "icon": "anydesk", "command": ["anydesk"]},
            {
                "label": "LocalSend",
                "icon": "localsend_app",
                "command": ["localsend_app"],
            },
        ]
        window._launch = Mock()

        bar = window._build_detach_bar()
        buttons = {
            child.get_tooltip_text(): child
            for child in bar.get_children()
            if isinstance(child, Gtk.Button)
        }
        buttons["AnyDesk"].emit("clicked")
        buttons["LocalSend"].emit("clicked")

        icon_sizes = {
            label: button.get_child().get_pixel_size()
            for label, button in buttons.items()
        }
        detach_icon_size = icon_sizes[
            "Stacca in una finestra sul monitor principale"
        ]
        self.assertGreater(detach_icon_size, 0)
        self.assertEqual(set(icon_sizes.values()), {detach_icon_size})
        self.assertEqual(
            window._launch.call_args_list,
            [unittest.mock.call(["anydesk"]), unittest.mock.call(["localsend_app"])],
        )
        self.assertEqual(
            buttons["AnyDesk"].get_child().get_icon_name()[0],
            "anydesk",
        )
        self.assertEqual(
            buttons["LocalSend"].get_child().get_icon_name()[0],
            "localsend_app",
        )
        bar.destroy()

    def test_detaching_the_last_tab_leaves_the_area_empty(self):
        # Nessuna scheda di rimpiazzo: le linguette sono nascoste, quindi una
        # scheda che nessun pulsante possiede sarebbe un processo invisibile
        # che nessuno potrebbe piu' raggiungere ne' chiudere.
        window = self._window()
        notebook = Gtk.Notebook()
        window.terminal_notebook = notebook
        page = Gtk.Box()
        window.terminals = {"zsh": page}
        notebook.append_page(page, Gtk.Label(label="Zsh"))
        # Un notebook sceglie solo pagine visibili: senza questo la scheda
        # corrente resta -1 e il test proverebbe il ramo sbagliato.
        notebook.show_all()
        notebook.set_current_page(0)
        window._place_detached = Mock()

        window._detach_current_page()

        self.assertEqual(notebook.get_n_pages(), 0)
        self.assertEqual(list(window.detached), ["zsh"])

    def test_detach_button_in_the_new_window_reattaches_the_tab(self):
        window = self._window()
        window.present = Mock()
        notebook = Gtk.Notebook()
        window.terminal_notebook = notebook
        page = Gtk.Box()
        window.terminals = {"zsh": page}
        notebook.append_page(page, Gtk.Label(label="Zsh"))
        notebook.show_all()
        notebook.set_current_page(0)
        window._place_detached = Mock()
        window._reload_page = Mock()

        window._detach_current_page()

        detached = window.detached["zsh"]
        reload_button = next(
            child for child in detached.get_titlebar().get_children()
            if child.get_tooltip_text() == "Ricarica il tool corrente"
        )
        reattach_button = next(
            child for child in detached.get_titlebar().get_children()
            if child.get_tooltip_text() == "Riattacca al pannello"
        )
        self.assertGreater(reload_button.get_child().get_pixel_size(), 0)
        self.assertEqual(
            reload_button.get_child().get_pixel_size(),
            reattach_button.get_child().get_pixel_size(),
        )
        reload_button.emit("clicked")
        reattach_button.emit("clicked")

        window._reload_page.assert_called_once_with(page)
        self.assertEqual(window.detached, {})
        self.assertEqual(notebook.get_n_pages(), 1)
        self.assertIs(notebook.get_nth_page(0), page)

    def test_reload_button_restarts_the_current_terminal(self):
        window = self._window()
        notebook = Gtk.Notebook()
        window.terminal_notebook = notebook
        previous = Gtk.Box()
        replacement = Gtk.Box()
        window.terminals = {"codex": previous}
        window.panel_width = 400
        window._build_terminal = Mock(return_value=replacement)
        notebook.append_page(previous, Gtk.Label(label="Codex"))
        notebook.show_all()
        notebook.set_current_page(0)

        window._reload_current_page()

        window._build_terminal.assert_called_once_with("codex", width_px=400)
        self.assertIs(window.terminals["codex"], replacement)
        self.assertIs(notebook.get_nth_page(0), replacement)
        self.assertEqual(notebook.get_tab_label_text(replacement), "Codex")

    def test_reload_button_reloads_the_current_web_app(self):
        window = self._window()
        webview = Mock()
        window.embedded = {"url:https://example.com": webview}

        window._reload_page(webview)

        webview.reload.assert_called_once_with()

    def test_a_returning_tab_is_reachable_from_its_button_again(self):
        window = self._window()
        window.present = Mock()
        notebook = Gtk.Notebook()
        window.terminal_notebook = notebook
        page = Gtk.Box()
        window.terminals = {"zsh": page}
        detached = Gtk.Window(title="Zsh \u2014 ai-bar")
        detached.add(page)
        window.detached = {"zsh": detached}

        window._on_detached_closed(detached, None, "zsh")

        self.assertEqual(notebook.get_n_pages(), 1)
        self.assertIs(window.terminals["zsh"], page)
        self.assertEqual(window.detached, {})

    def test_an_embedded_window_cannot_be_detached(self):
        # Staccare un Gtk.Socket vuol dire smontare l'incorporamento.
        window = self._window()

        self.assertFalse(window._detachable_page(Gtk.Socket()))

    def test_the_page_key_is_found_among_both_kinds_of_tab(self):
        window = self._window()
        terminal, chat, stranger = Gtk.Box(), Gtk.Box(), Gtk.Box()
        window.terminals = {"claude": terminal}
        window.embedded = {"url:https://example.com": chat}

        self.assertEqual(window._page_key(terminal), "claude")
        self.assertEqual(window._page_key(chat), "url:https://example.com")
        self.assertIsNone(window._page_key(stranger))

    def test_clicking_a_detached_tool_raises_its_window(self):
        # Senza questo page_num tornerebbe -1 e set_current_page(-1)
        # selezionerebbe l'ultima scheda, in silenzio.
        window = self._window()
        window.terminal_notebook = Mock()
        detached = Mock()
        window.detached = {terminal_session_key(["claude"]): detached}

        window._switch_terminal(["claude"], "Claude")

        detached.present.assert_called_once()
        window.terminal_notebook.set_current_page.assert_not_called()
        window.terminal_notebook.append_page.assert_not_called()

    @patch("ai_bar.app.WebKit2")
    def test_clicking_a_detached_web_app_raises_its_window(self, _webkit2):
        window = self._window()
        window.terminal_notebook = Mock()
        detached = Mock()
        window.detached = {"url:https://example.com": detached}

        window._switch_webview("https://example.com", "Chat")

        detached.present.assert_called_once()
        window.terminal_notebook.set_current_page.assert_not_called()

    def test_a_detached_tool_is_marked_on_its_button(self):
        window = self._window()
        claude, shell = Gtk.Button(), Gtk.Button()
        window.launcher_buttons = {"claude": claude, "bash": shell}
        window.terminal_notebook = None
        window.detached = {"claude": Mock()}

        window._refresh_launcher_states()

        self.assertTrue(claude.get_style_context().has_class("detached-launcher"))
        self.assertFalse(shell.get_style_context().has_class("detached-launcher"))

        window.detached = {}
        window._refresh_launcher_states()
        self.assertFalse(claude.get_style_context().has_class("detached-launcher"))

    def test_the_switched_page_wins_over_the_notebook_state(self):
        # Durante switch-page il notebook non ha ancora aggiornato la propria
        # pagina corrente: chiederglielo darebbe quella di prima, e il pulsante
        # "stacca" resterebbe spento sulla scheda appena aperta.
        window = self._window()
        window.home_page = Gtk.Box()
        window.detach_button = Gtk.Button()
        notebook = Mock()
        notebook.get_current_page.return_value = 0
        notebook.get_nth_page.return_value = Gtk.Socket()
        window.terminal_notebook = notebook

        window._refresh_launcher_states(Gtk.Box())
        self.assertTrue(window.detach_button.get_sensitive())

        window._refresh_launcher_states()
        self.assertFalse(window.detach_button.get_sensitive())

    def test_closing_a_detached_window_puts_the_tab_back(self):
        window = self._window()
        window.present = Mock()
        notebook = Gtk.Notebook()
        window.terminal_notebook = notebook
        page = Gtk.Box()
        detached = Gtk.Window(title="Claude \u2014 ai-bar")
        detached.add(page)
        window.detached = {"claude": detached}

        handled = window._on_detached_closed(detached, None, "claude")

        # True: la finestra la distruggiamo noi, dopo aver messo al sicuro
        # il contenuto.
        self.assertTrue(handled)
        self.assertEqual(window.detached, {})
        self.assertEqual(notebook.get_n_pages(), 1)
        self.assertIs(notebook.get_nth_page(0), page)
        self.assertEqual(notebook.get_tab_label_text(page), "Claude")


class FaviconTests(unittest.TestCase):
    def _window(self):
        window = AiBarWindow.__new__(AiBarWindow)
        window.favicon_targets = {}
        window.favicon_fetched = set()
        return window

    def test_same_origin_accepts_the_same_site(self):
        self.assertTrue(same_origin("https://www.example.net/app/login.php",
                                    "https://www.example.net/app/"))

    def test_same_origin_rejects_a_different_host_or_scheme(self):
        self.assertFalse(same_origin("https://evil.example.com/icon.svg",
                                     "https://www.example.net/app/"))
        self.assertFalse(same_origin("http://www.example.net/app/",
                                     "https://www.example.net/app/"))
        self.assertFalse(same_origin("https://www.example.net:8443/app/",
                                     "https://www.example.net/app/"))

    def test_same_origin_rejects_junk(self):
        for value in ("", "about:blank", "not a url"):
            with self.subTest(value=value):
                self.assertFalse(same_origin(value, "https://www.example.net/app/"))

    def test_cache_path_is_stable_and_specific_to_the_site(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
                first = favicon_cache_path("https://a.example.net/")
                again = favicon_cache_path("https://a.example.net/")
                other = favicon_cache_path("https://b.example.net/")

        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertEqual(first.suffix, ".png")
        self.assertEqual(first.parent.name, "icons")

    def test_download_refuses_an_icon_from_another_origin(self):
        # L'indirizzo dell'icona arriva dalla pagina: dopo un redirect altrove
        # non deve diventare una richiesta verso un sito qualsiasi.
        window = self._window()

        with patch("ai_bar.app.threading.Thread") as thread:
            window._download_favicon(Mock(), "https://www.example.net/app/",
                                     "https://evil.example.com/icon.svg")

        thread.assert_not_called()
        self.assertEqual(window.favicon_fetched, set())

    def test_download_happens_once_per_icon(self):
        # favicon-changed puo' ripetersi a ogni caricamento della pagina.
        window = self._window()
        icon = "https://www.example.net/favicon.svg"

        with patch("ai_bar.app.threading.Thread") as thread:
            for _ in range(3):
                window._download_favicon(Mock(), "https://www.example.net/app/", icon)

        self.assertEqual(thread.call_count, 1)
        self.assertEqual(window.favicon_fetched, {icon})

    def test_download_ignores_a_missing_icon_address(self):
        window = self._window()

        with patch("ai_bar.app.threading.Thread") as thread:
            window._download_favicon(Mock(), "https://www.example.net/app/", None)

        thread.assert_not_called()

    def test_a_changed_favicon_reaches_the_button_of_the_same_site(self):
        # La pagina che porta l'icona e' quella effettiva (il login sta spesso
        # altrove): conta l'origine, non l'indirizzo esatto.
        window = self._window()
        window._apply_favicon = Mock()
        image = Mock()
        window.favicon_targets["https://www.example.net/app/"] = image

        window._on_favicon_changed(None, "https://www.example.net/app/login.php",
                                   "https://www.example.net/favicon.svg")
        window._apply_favicon.assert_called_once_with(
            image, "https://www.example.net/app/",
            "https://www.example.net/app/login.php",
            "https://www.example.net/favicon.svg")

        window._apply_favicon.reset_mock()
        window._on_favicon_changed(None, "https://other.example.com/",
                                   "https://other.example.com/favicon.ico")
        window._apply_favicon.assert_not_called()


class KeyringLoginTests(unittest.TestCase):
    def _window(self, credentials=None):
        window = AiBarWindow.__new__(AiBarWindow)
        window._web_credentials = Mock(return_value=credentials)
        return window

    def test_login_is_filled_on_the_site_it_was_saved_for(self):
        window = self._window(("ada", "hunter2"))
        view = Mock()
        view.get_uri.return_value = "https://www.example.net/app/login.php"

        window._fill_login(view, "https://www.example.net/app/")

        view.evaluate_javascript.assert_called_once()
        script = view.evaluate_javascript.call_args.args[0]
        self.assertIn('"ada"', script)

    def test_login_is_not_filled_after_a_redirect_elsewhere(self):
        # Il caso che conta: riempire il form su un'altra origine vorrebbe dire
        # consegnare la password a qualcun altro.
        window = self._window(("ada", "hunter2"))
        view = Mock()
        view.get_uri.return_value = "https://evil.example.com/login"

        window._fill_login(view, "https://www.example.net/app/")

        view.evaluate_javascript.assert_not_called()

    def test_nothing_is_filled_without_a_stored_login(self):
        # La voce nel portachiavi fa da interruttore: senza, la compilazione
        # automatica semplicemente non avviene.
        window = self._window(None)
        view = Mock()
        view.get_uri.return_value = "https://www.example.net/app/"

        window._fill_login(view, "https://www.example.net/app/")

        view.evaluate_javascript.assert_not_called()

    def test_a_submitted_login_is_offered_for_saving(self):
        window = self._window(None)
        window._store_credentials = Mock()
        with patch("ai_bar.app.Gtk.MessageDialog") as dialog_class:
            dialog = dialog_class.return_value
            dialog.run.return_value = Gtk.ResponseType.YES
            window._on_login_submitted(
                "https://www.example.net/app/",
                '{"username": "ada", "password": "hunter2"}')

        window._store_credentials.assert_called_once_with(
            "https://www.example.net/app/", "ada", "hunter2")

    def test_a_refused_login_is_not_stored(self):
        window = self._window(None)
        window._store_credentials = Mock()
        with patch("ai_bar.app.Gtk.MessageDialog") as dialog_class:
            dialog_class.return_value.run.return_value = Gtk.ResponseType.NO
            window._on_login_submitted(
                "https://www.example.net/app/",
                '{"username": "ada", "password": "hunter2"}')

        window._store_credentials.assert_not_called()

    def test_an_unchanged_login_is_not_asked_about_again(self):
        # Senza questo la domanda tornerebbe a ogni accesso.
        window = self._window(("ada", "hunter2"))
        window._store_credentials = Mock()
        with patch("ai_bar.app.Gtk.MessageDialog") as dialog_class:
            window._on_login_submitted(
                "https://www.example.net/app/",
                '{"username": "ada", "password": "hunter2"}')

        dialog_class.assert_not_called()
        window._store_credentials.assert_not_called()

    def test_an_incomplete_submission_is_ignored(self):
        window = self._window(None)
        window._store_credentials = Mock()
        with patch("ai_bar.app.Gtk.MessageDialog") as dialog_class:
            for payload in ("", "not json", "{}",
                            '{"username": "ada", "password": ""}'):
                with self.subTest(payload=payload):
                    window._on_login_submitted("https://www.example.net/app/", payload)

        dialog_class.assert_not_called()
        window._store_credentials.assert_not_called()

if __name__ == "__main__":
    unittest.main()
