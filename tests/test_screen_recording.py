import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_bar.app import AiBarWindow, RecordingAreaSelector, recording_audio_sources
from gi.repository import Gdk, Gio, GLib, Gtk


class RecordingAudioSourceTests(unittest.TestCase):
    @patch("ai_bar.app.run_text_command")
    def test_sources_distinguish_microphones_from_computer_audio(self, run):
        run.return_value = json.dumps([
            {"name": "alsa_input.internal", "description": "Microfono integrato"},
            {"name": "bluez_input.headset", "description": "Cuffie Bluetooth"},
            {"name": "alsa_output.speakers.monitor", "description": "Altoparlanti",
             "monitor_source": "alsa_output.speakers"},
            {"name": "custom_loopback", "description": "USB",
             "properties": {"device.class": "monitor"}},
        ])
        self.assertEqual(recording_audio_sources(), [
            ("alsa_input.internal", "Microfono / ingresso — Microfono integrato"),
            ("bluez_input.headset", "Microfono / ingresso — Cuffie Bluetooth"),
            ("alsa_output.speakers.monitor", "Audio del computer — Altoparlanti"),
            ("custom_loopback", "Audio del computer — USB"),
        ])
        run.assert_called_once_with(["pactl", "--format=json", "list", "sources"])

    @patch("ai_bar.app.run_text_command")
    def test_missing_audio_server_or_invalid_response_leaves_video_available(self, run):
        for output in ("", "not json", "null", "{}", "[]"):
            with self.subTest(output=output):
                run.return_value = output
                self.assertEqual(recording_audio_sources(), [])

    @patch("ai_bar.app.run_text_command")
    def test_missing_description_uses_source_name_and_invalid_entries_are_skipped(self, run):
        run.return_value = json.dumps([{}, None, {"name": "usb_mic"}, {"name": "speakers.monitor"}])
        self.assertEqual(recording_audio_sources(), [
            ("usb_mic", "Microfono / ingresso — usb_mic"),
            ("speakers.monitor", "Audio del computer — speakers.monitor"),
        ])

    @patch("ai_bar.app.run_text_command")
    def test_muted_microphone_is_identified_without_changing_device_settings(self, run):
        run.return_value = json.dumps([
            {"name": "mic", "description": "Integrato", "monitor_source": None, "mute": True},
        ])
        self.assertEqual(recording_audio_sources(), [
            ("mic", "Microfono / ingresso — Integrato (silenziato)"),
        ])
        run.assert_called_once_with(["pactl", "--format=json", "list", "sources"])

    @patch("ai_bar.app.recording_audio_sources")
    def test_dialog_refreshes_devices_keeps_selection_and_supports_cancel(self, sources):
        sources.return_value = [("mic", "Microfono integrato"), ("output.monitor", "Audio del computer")]
        window = AiBarWindow.__new__(AiBarWindow)
        Gtk.Window.__init__(window)
        self.addCleanup(window.destroy)
        window.recording_audio_source = "mic"
        dialogs = []
        responses = [(Gtk.ResponseType.OK, "output.monitor"), (Gtk.ResponseType.CANCEL, "mic")]
        def respond(dialog):
            dialogs.append(dialog)
            combo = next(child for child in dialog.get_content_area().get_children()
                         if isinstance(child, Gtk.ComboBoxText))
            self.assertEqual(combo.get_active_id(), "mic")
            response, selection = responses.pop(0)
            combo.set_active_id(selection)
            return response
        with patch.object(Gtk.Dialog, "run", respond):
            self.assertEqual(window._choose_recording_audio_source(), "output.monitor")
            self.assertIsNone(window._choose_recording_audio_source())
        self.assertEqual(sources.call_count, 2)
        self.assertTrue(all(not dialog.get_visible() for dialog in dialogs))

    @patch("ai_bar.app.recording_audio_sources", return_value=[])
    def test_disconnected_device_falls_back_to_no_audio(self, sources):
        window = AiBarWindow.__new__(AiBarWindow)
        Gtk.Window.__init__(window)
        self.addCleanup(window.destroy)
        window.recording_audio_source = "disconnected_usb_mic"
        def accept(dialog):
            combo = next(child for child in dialog.get_content_area().get_children()
                         if isinstance(child, Gtk.ComboBoxText))
            self.assertEqual(combo.get_active_id(), "")
            return Gtk.ResponseType.OK
        with patch.object(Gtk.Dialog, "run", accept):
            self.assertEqual(window._choose_recording_audio_source(), "")


class RecordingAreaSelectorTests(unittest.TestCase):
    def choose(self, interact):
        window = AiBarWindow.__new__(AiBarWindow)
        errors = []
        def run_interaction():
            selector = next(widget for widget in Gtk.Window.list_toplevels()
                            if isinstance(widget, RecordingAreaSelector))
            try:
                interact(selector)
            except Exception as exc:
                errors.append(exc)
                selector.response(Gtk.ResponseType.CANCEL)
            return False
        GLib.idle_add(run_interaction)
        result = window._choose_recording_area()
        if errors:
            raise errors[0]
        self.assertFalse(any(isinstance(widget, RecordingAreaSelector) and widget.get_visible()
                             for widget in Gtk.Window.list_toplevels()))
        return result

    def emit_pointer(self, selector, signal_name, event_type, x, y):
        event = Gdk.Event.new(event_type)
        event.window = selector.get_window()
        event.x, event.y = x, y
        if event_type != Gdk.EventType.MOTION_NOTIFY:
            event.button = 1
        selector.emit(signal_name, event)

    def drag(self, selector, start, end):
        self.emit_pointer(selector, "button-press-event", Gdk.EventType.BUTTON_PRESS, *start)
        self.emit_pointer(selector, "motion-notify-event", Gdk.EventType.MOTION_NOTIFY, *end)
        self.emit_pointer(selector, "button-release-event", Gdk.EventType.BUTTON_RELEASE, *end)

    def test_drag_in_both_directions_returns_the_selected_screen_coordinates(self):
        for start, end in (((50, 60), (691, 539)), ((691, 539), (50, 60))):
            with self.subTest(start=start):
                area = self.choose(lambda selector: self.drag(selector, start, end))
                self.assertEqual((area.x, area.y, area.width, area.height), (50, 60, 641, 479))

    def test_selection_is_clamped_to_desktop_bounds(self):
        root = Gdk.get_default_root_window()
        area = self.choose(lambda selector: self.drag(
            selector, (root.get_width() - 100, root.get_height() - 100),
            (root.get_width() + 100, root.get_height() + 100)))
        self.assertEqual((area.width, area.height), (100, 100))

    def test_escape_cancels_and_a_click_without_drag_does_not_record(self):
        def interact(selector):
            responses = []
            selector.connect("response", lambda _dialog, response: responses.append(response))
            self.drag(selector, (50, 60), (50, 60))
            self.assertEqual(responses, [])
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval = Gdk.KEY_Escape
            selector.emit("key-press-event", event)
            self.assertEqual(responses, [Gtk.ResponseType.CANCEL])
        self.assertIsNone(self.choose(interact))

    def test_scaled_desktop_returns_physical_pixels_for_ffmpeg(self):
        with patch.object(RecordingAreaSelector, "get_scale_factor", return_value=2):
            area = self.choose(lambda selector: self.drag(selector, (50, 60), (691, 539)))
        self.assertEqual((area.x, area.y, area.width, area.height), (100, 120, 1282, 958))

    def test_unavailable_input_grab_cancels_selection(self):
        window = AiBarWindow.__new__(AiBarWindow)
        with patch.object(Gdk.Seat, "grab", return_value=Gdk.GrabStatus.ALREADY_GRABBED):
            self.assertIsNone(window._choose_recording_area())


class ScreenRecordingTests(unittest.TestCase):
    def setUp(self):
        self.window = AiBarWindow.__new__(AiBarWindow)
        self.window.recording_process = None
        self.window.recording_path = None
        self.window.recording_stopping = False
        self.window.recording_audio_source = ""
        self.window._choose_recording_audio_source = Mock(return_value="")
        self.window._choose_recording_area = Mock(return_value=SimpleNamespace(
            x=2021, y=137, width=641, height=479))
        self.window._show_error = Mock()
        self.window._monitor_geometry = Mock(return_value=SimpleNamespace(
            x=1920, y=0, width=1080, height=1920))
        self.button = self.window._build_screen_recording_button()
        self.addCleanup(self.button.destroy)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patcher = patch("ai_bar.app.GLib.get_user_special_dir", return_value=self.temporary.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("ai_bar.app.Gio.Subprocess.new")
    @patch.dict(os.environ, {"DISPLAY": ":7", "XDG_SESSION_TYPE": "x11"})
    def test_click_starts_recording_and_second_click_stops_then_saves(self, spawn):
        process = spawn.return_value
        self.button.clicked()
        argv, flags = spawn.call_args.args
        self.assertEqual(argv[0], "ffmpeg")
        self.assertEqual(argv[argv.index("-video_size") + 1], "641x479")
        self.assertEqual(argv[argv.index("-i") + 1], ":7+2021,137")
        self.assertEqual(argv[argv.index("-vf") + 1], "pad=ceil(iw/2)*2:ceil(ih/2)*2")
        self.assertEqual(argv[argv.index("-framerate") + 1], "30")
        self.assertEqual(Path(argv[-1]).parent, Path(self.temporary.name) / "Registrazioni")
        self.assertEqual(Path(argv[-1]).suffix, ".mp4")
        self.assertFalse(flags & Gio.SubprocessFlags.STDIN_PIPE)
        self.assertTrue(self.button.get_style_context().has_class("recording"))
        self.assertEqual(self.button.get_tooltip_text(), "Ferma e salva la registrazione")
        self.window._choose_recording_audio_source.assert_called_once_with()
        self.window._choose_recording_area.assert_called_once_with()
        self.button.clicked()
        self.window._choose_recording_audio_source.assert_called_once_with()
        self.window._choose_recording_area.assert_called_once_with()
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertFalse(self.button.get_sensitive())
        self.assertIs(self.window.recording_process, process)
        process.communicate_utf8_finish.return_value = (True, None, "")
        process.get_successful.return_value = False
        process.get_if_exited.return_value = True
        process.get_exit_status.return_value = 255
        callback = process.communicate_utf8_async.call_args.args[2]
        callback(process, Mock())
        self.assertIsNone(self.window.recording_process)
        self.assertTrue(self.button.get_sensitive())
        self.assertFalse(self.button.get_style_context().has_class("recording"))
        self.assertIn(argv[-1], self.button.get_tooltip_text())
        self.window._show_error.assert_not_called()

    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_cancel_does_not_start_a_recording_or_create_a_file(self, spawn):
        self.window._choose_recording_audio_source.return_value = None
        self.button.clicked()
        self.window._choose_recording_area.assert_not_called()
        spawn.assert_not_called()
        self.assertIsNone(self.window.recording_process)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_cancel_area_selection_does_not_start_recording_or_create_a_file(self, spawn):
        self.window._choose_recording_area.return_value = None
        self.button.clicked()
        spawn.assert_not_called()
        self.assertIsNone(self.window.recording_process)
        self.assertTrue(self.button.get_sensitive())
        self.assertFalse(self.button.get_style_context().has_class("recording"))
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_selected_microphone_and_system_audio_are_captured_as_aac(self, spawn):
        for source in ("alsa_input.laptop", "alsa_output.headphones.monitor"):
            with self.subTest(source=source):
                self.window.recording_process = None
                self.window._choose_recording_audio_source.return_value = source
                self.button.clicked()
                argv = spawn.call_args.args[0]
                inputs = [argv[i + 1] for i, option in enumerate(argv) if option == "-i"]
                self.assertEqual(inputs[1], source)
                self.assertIn("pulse", argv)
                self.assertNotIn("-an", argv)
                self.assertEqual(argv[argv.index("-c:a") + 1], "aac")
                self.assertEqual(argv[argv.index("-af") + 1], "aresample=async=1:first_pts=0")
                self.assertEqual(self.window.recording_audio_source, source)

    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_without_audio_has_no_audio_input(self, spawn):
        self.button.clicked()
        argv = spawn.call_args.args[0]
        self.assertIn("-an", argv)
        self.assertNotIn("pulse", argv)
        self.assertEqual(argv.count("-i"), 1)

    @patch("ai_bar.app.Gio.Subprocess.new", side_effect=GLib.Error("ffmpeg assente"))
    def test_launch_error_leaves_button_ready(self, spawn):
        self.button.clicked()
        self.assertIsNone(self.window.recording_process)
        self.assertTrue(self.button.get_sensitive())
        self.assertFalse(self.button.get_style_context().has_class("recording"))
        self.window._show_error.assert_called_once()

    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_encoder_failure_is_reported_and_allows_retry(self, spawn):
        process = spawn.return_value
        self.button.clicked()
        process.communicate_utf8_finish.return_value = (True, None, "Cannot open display")
        process.get_successful.return_value = False
        callback = process.communicate_utf8_async.call_args.args[2]
        callback(process, Mock())
        self.assertIsNone(self.window.recording_process)
        self.assertEqual(self.button.get_tooltip_text(), "Registra lo schermo")
        self.window._show_error.assert_called_once()
        self.assertIn("Cannot open display", self.window._show_error.call_args.args[0])

    @patch("ai_bar.app.Gio.Subprocess.new")
    def test_shutdown_stops_and_waits_for_video_finalization(self, spawn):
        self.button.clicked()
        process = spawn.return_value
        self.window._stop_screen_recording(wait=True)
        process.send_signal.assert_called_once_with(signal.SIGINT)
        process.wait.assert_called_once_with(None)
        self.assertIsNone(self.window.recording_process)

    @patch("ai_bar.app.os.execvp")
    @patch("ai_bar.app.Gtk.main_quit")
    def test_reload_and_destroy_finalize_the_recording(self, quit_main, execvp):
        window = SimpleNamespace(
            _stop_screen_recording=Mock(), xapp_tray_host=None, tray_host=None,
            embedded_window_xids={},
            volume_update_timeout_id=None, panel_animation_id=None,
            window_list_poll_id=None, super_toggle=None)
        AiBarWindow._reload(window)
        window._stop_screen_recording.assert_called_once_with(wait=True)
        execvp.assert_called_once()
        window._stop_screen_recording.reset_mock()
        AiBarWindow._on_destroy(window, window)
        window._stop_screen_recording.assert_called_once_with(wait=True)
        quit_main.assert_called_once_with()

    @patch("ai_bar.app.Gio.Subprocess.new")
    @patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"})
    def test_wayland_reports_unsupported_capture(self, spawn):
        self.button.clicked()
        spawn.assert_not_called()
        self.window._show_error.assert_called_once()
