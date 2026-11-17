import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_bar.app import AiBarWindow, GdkX11, Gtk, X, X11WindowCycle


class WindowCycleTests(unittest.TestCase):
    def test_cycles_in_stable_order_including_minimized_windows(self):
        windows = []
        for xid in (10, 20, 30, 40, 50):
            window = Mock()
            window.get_xid.return_value = xid
            window.is_skip_tasklist.return_value = xid == 40
            window.is_pinned.return_value = False
            window.is_on_workspace.return_value = xid != 50
            windows.append(window)
        screen = Mock()
        screen.get_windows.return_value = windows
        controller = SimpleNamespace(wnck_screen=screen, own_xid=99)
        # Raising a window changes the stacking order, but must not change the cycle.
        for active, target in ((windows[0], windows[1]), (windows[1], windows[2]),
                               (windows[2], windows[0]), (None, windows[0])):
            screen.get_active_window.return_value = active
            for window in windows:
                window.activate.reset_mock()
            AiBarWindow._cycle_window(controller, 123)
            target.activate.assert_called_once_with(123)
            for window in windows:
                if window is not target:
                    window.activate.assert_not_called()

    def test_no_windows_or_no_wnck_is_harmless(self):
        AiBarWindow._cycle_window(SimpleNamespace(wnck_screen=None), 123)
        screen = Mock()
        screen.get_windows.return_value = []
        AiBarWindow._cycle_window(SimpleNamespace(wnck_screen=screen, own_xid=99), 123)

    @patch('ai_bar.app.GLib.source_remove')
    @patch('ai_bar.app.GLib.io_add_watch', return_value=42)
    @patch('ai_bar.app.xlib_display')
    def test_grabs_only_shift_tab_with_lock_variants_and_cleans_up(self, xdisplay, watch, remove):
        display = xdisplay.Display.return_value
        display.keysym_to_keycode.side_effect = [23, 77]
        display.get_modifier_mapping.return_value = [[], [], [], [], [77], [], [], []]
        callback = Mock()
        shortcut = X11WindowCycle(callback)
        self.assertTrue(shortcut.start())
        root = display.screen.return_value.root
        self.assertEqual(
            {call.args[1] for call in root.grab_key.call_args_list},
            {X.ShiftMask, X.ShiftMask | X.LockMask,
             X.ShiftMask | X.Mod2Mask, X.ShiftMask | X.LockMask | X.Mod2Mask},
        )
        self.assertTrue(all(call.args[0] == 23 and call.args[2] is False
                            for call in root.grab_key.call_args_list))
        display.pending_events.side_effect = [2, 1, 0]
        display.next_event.side_effect = [
            SimpleNamespace(type=X.KeyPress, detail=23, time=123),
            SimpleNamespace(type=X.KeyRelease, detail=23, time=124),
        ]
        self.assertTrue(shortcut._on_events(None, None))
        callback.assert_called_once_with(123)
        shortcut.stop()
        remove.assert_called_once_with(42)
        display.close.assert_called_once_with()
        self.assertIsNone(shortcut.display)

    @patch('ai_bar.app.GLib.io_add_watch')
    @patch('ai_bar.app.xlib_display')
    def test_grab_conflict_disables_shortcut_and_closes_connection(self, xdisplay, watch):
        display = xdisplay.Display.return_value
        display.keysym_to_keycode.side_effect = [23, 77]
        display.get_modifier_mapping.return_value = [[], [], [], [], [77], [], [], []]
        display.screen.return_value.root.grab_key.side_effect = (
            lambda *args, **kwargs: kwargs['onerror'](Mock(), None)
        )
        shortcut = X11WindowCycle(Mock())
        self.assertFalse(shortcut.start())
        watch.assert_not_called()
        display.close.assert_called_once_with()

    @patch('ai_bar.app.GLib.idle_add')
    @patch('ai_bar.app.X11SuperToggle')
    @patch('ai_bar.app.X11WindowCycle')
    @patch.dict('os.environ', {}, clear=True)
    def test_shortcut_starts_only_for_x11_with_window_list(self, cycle, _super, _idle):
        for gdk_window, wnck_screen, expected in (
            (Mock(spec=GdkX11.X11Window), Mock(), True),
            (object(), Mock(), False),
            (Mock(spec=GdkX11.X11Window), None, False),
        ):
            cycle.reset_mock()
            window = SimpleNamespace(
                get_window=lambda: gdk_window, wnck_screen=wnck_screen,
                _apply_panel_geometry=Mock(), _apply_strut=Mock(),
                _start_window_list=Mock(), _toggle_panel_visibility=Mock(),
                _focus_clicked_embedded_window=Mock(), _focus_terminal=Mock(),
                _restore_native_windows_after_reload=Mock(), _cycle_window=Mock(),
                xapp_tray_host=None, tray_host=None,
            )
            with patch('ai_bar.app.GdkX11.X11Window.get_xid', return_value=99):
                AiBarWindow._on_realize(window, None)
            if expected:
                cycle.assert_called_once_with(window._cycle_window)
                cycle.return_value.start.assert_called_once_with()
            else:
                cycle.assert_not_called()

    @patch('ai_bar.app.Gtk.main_quit')
    def test_destroy_releases_shortcut(self, _quit):
        window = SimpleNamespace(
            _stop_screen_recording=Mock(), volume_update_timeout_id=None,
            panel_animation_id=None, window_list_poll_id=None, super_toggle=None,
            window_cycle=Mock(), xapp_tray_host=None, tray_host=None,
        )
        AiBarWindow._on_destroy(window, None)
        window.window_cycle.stop.assert_called_once_with()

    def test_cycle_returns_to_panel_even_though_it_skips_tasklist(self):
        panel, external = Mock(), Mock()
        for window, xid in ((panel, 99), (external, 10)):
            window.get_xid.return_value = xid
            window.is_skip_tasklist.return_value = window is panel
            window.is_on_workspace.return_value = True
        screen = Mock()
        screen.get_windows.return_value = [panel, external]
        screen.get_active_window.return_value = external
        host = Gtk.Window()
        self.addCleanup(host.destroy)
        page = Gtk.Entry()
        notebook = Gtk.Notebook()
        notebook.append_page(page, Gtk.Label(label="Conversation"))
        host.add(notebook)
        host.show_all()
        host.set_focus(None)
        self.assertIsNone(host.get_focus())
        controller = SimpleNamespace(
            wnck_screen=screen, own_xid=99, panel_hidden=False,
            terminal_notebook=notebook, present_with_time=Mock(),
            get_window=Mock(return_value=Mock()),
        )
        AiBarWindow._cycle_window(controller, 123)
        controller.present_with_time.assert_called_once_with(123)
        controller.get_window.return_value.focus.assert_called_once_with(123)
        self.assertIs(host.get_focus(), page)
        external.activate.assert_not_called()
        screen.get_active_window.return_value = panel
        AiBarWindow._cycle_window(controller, 124)
        external.activate.assert_called_once_with(124)

    @patch('ai_bar.app.GLib.idle_add')
    @patch('ai_bar.app.GLib.source_remove')
    def test_hidden_panel_is_shown_and_embedded_content_recovers_focus(self, remove, idle):
        panel = Mock()
        panel.get_xid.return_value = 99
        panel.is_skip_tasklist.return_value = True
        screen = Mock()
        screen.get_windows.return_value = [panel]
        screen.get_active_window.return_value = None
        socket = Gtk.Socket()
        notebook = Mock()
        notebook.get_nth_page.return_value = socket
        controller = SimpleNamespace(
            wnck_screen=screen, own_xid=99, panel_hidden=True, panel_animation_id=42,
            terminal_notebook=notebook, present_with_time=Mock(),
            get_window=Mock(return_value=Mock()), _apply_panel_geometry=Mock(),
            _apply_strut=Mock(), _focus_embedded_window=Mock(),
        )
        AiBarWindow._cycle_window(controller, 123)
        self.assertFalse(controller.panel_hidden)
        self.assertIsNone(controller.panel_animation_id)
        remove.assert_called_once_with(42)
        controller._apply_panel_geometry.assert_called_once_with()
        controller._apply_strut.assert_called_once_with()
        idle.assert_called_once_with(controller._focus_embedded_window, socket)
