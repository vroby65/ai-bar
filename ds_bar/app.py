from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, GdkX11, GLib, Gtk, Pango, Vte

from .config import ConfigError, default_config, load_config
from .xembed_tray import XEmbedTrayHost


CSS = """
#ds-bar {
  background: #151819;
  color: #f2f2ee;
}

.clock-time {
  font-size: 30px;
  font-weight: 700;
}

.clock-date {
  color: #aeb8bd;
  font-size: 13px;
}

.section-title {
  color: #9ea7ac;
  font-size: 11px;
  font-weight: 700;
  margin-top: 2px;
}

button.status-button,
button.launcher-button,
button.session-button {
  background: #242829;
  border: 1px solid #303638;
  border-radius: 6px;
  color: #f2f2ee;
  padding: 6px;
}

button.status-button:hover,
button.launcher-button:hover,
button.session-button:hover {
  background: #2d3335;
  border-color: #4c8f72;
}

button.launcher-button {
  min-height: 60px;
}

.terminal-wrap {
  border-top: 1px solid #303638;
  margin-top: 2px;
}

.resize-handle {
  background: #252a2c;
  min-width: 6px;
}

.resize-handle:hover {
  background: #4c8f72;
}

.session-row {
  border-top: 1px solid #303638;
  padding-top: 8px;
}
"""


class DsBarWindow(Gtk.Window):
    def __init__(self, config: dict[str, Any], config_path: Path | None = None) -> None:
        super().__init__(title="ds-bar")
        self.config = config
        self.config_path = config_path
        self.tray_host: XEmbedTrayHost | None = None
        self.status_labels: list[tuple[dict[str, Any], Gtk.Label]] = []
        self.terminal: Vte.Terminal | None = None
        self.panel_width = int(self.config["panel"]["width"])
        self.panel_geometry_applied = False
        self.resize_drag: tuple[int, float] | None = None

        panel = self.config["panel"]
        self.set_name("ds-bar")
        self.set_decorated(bool(panel.get("decorated", False)))
        self.set_keep_above(bool(panel.get("keep_above", True)))
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_accept_focus(True)
        self.set_focus_on_map(True)
        self.set_resizable(bool(panel.get("resizable", True)))
        self.connect("destroy", self._on_destroy)
        self.connect("realize", self._on_realize)
        self.connect("configure-event", self._on_configure)
        self.connect("key-press-event", self._on_key_press)

        self._install_css()
        self.add(self._build_content())
        self.show_all()

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_border_width(10)

        content.pack_start(self._build_clock(), False, False, 0)
        content.pack_start(self._build_tray_row(), False, False, 0)

        for group in self.config.get("launcher_groups", []):
            content.pack_start(self._build_launcher_group(group), False, False, 0)

        terminal_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        terminal_wrap.get_style_context().add_class("terminal-wrap")
        terminal_wrap.pack_start(self._build_terminal(), True, True, 8)
        content.pack_start(terminal_wrap, True, True, 0)
        content.pack_start(self._build_session_buttons(), False, False, 0)

        resizable = bool(self.config["panel"].get("resizable", True))
        if resizable and self.config["panel"].get("side", "left") == "right":
            root.pack_start(self._build_resize_handle(), False, False, 0)

        root.pack_start(content, True, True, 0)

        if resizable and self.config["panel"].get("side", "left") == "left":
            root.pack_start(self._build_resize_handle(), False, False, 0)

        return root

    def _build_clock(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.time_label = Gtk.Label()
        self.date_label = Gtk.Label()
        self.time_label.get_style_context().add_class("clock-time")
        self.date_label.get_style_context().add_class("clock-date")
        self.time_label.set_xalign(0.5)
        self.date_label.set_xalign(0.5)
        box.pack_start(self.time_label, False, False, 0)
        box.pack_start(self.date_label, False, False, 0)

        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)
        return box

    def _build_tray_row(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_homogeneous(False)

        for item in self.config.get("tray", {}).get("items", []):
            row.pack_start(self._build_status_button(item), False, False, 0)

        if self.config.get("tray", {}).get("xembed", True):
            self.tray_host = XEmbedTrayHost(row, int(self.config.get("tray", {}).get("icon_size", 24)))

        refresh = int(self.config.get("tray", {}).get("status_refresh_seconds", 5))
        if self.status_labels:
            self._update_status_items()
            GLib.timeout_add_seconds(max(1, refresh), self._update_status_items)

        return row

    def _build_status_button(self, item: dict[str, Any]) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("status-button")
        button.set_tooltip_text(str(item.get("label", "")))
        button.set_relief(Gtk.ReliefStyle.NONE)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        icon_name = item.get("icon")
        if icon_name:
            image = Gtk.Image.new_from_icon_name(str(icon_name), Gtk.IconSize.BUTTON)
            inner.pack_start(image, False, False, 0)

        label = Gtk.Label(label=str(item.get("label", "")))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(14)
        inner.pack_start(label, False, False, 0)
        button.add(inner)

        command = item.get("command")
        if command:
            button.connect("clicked", lambda _button: self._launch(command))

        if item.get("type") in {"volume", "wifi"}:
            self.status_labels.append((item, label))

        return button

    def _build_launcher_group(self, group: dict[str, Any]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        title = group.get("title")
        if title:
            label = Gtk.Label(label=str(title))
            label.set_xalign(0)
            label.get_style_context().add_class("section-title")
            box.pack_start(label, False, False, 0)

        columns = max(1, int(group.get("columns", 1)))
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.set_column_homogeneous(True)

        for index, button_config in enumerate(group.get("buttons", [])):
            button = self._build_launcher_button(button_config)
            grid.attach(button, index % columns, index // columns, 1, 1)

        box.pack_start(grid, False, False, 0)
        return box

    def _build_launcher_button(self, button_config: dict[str, Any]) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("launcher-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(str(button_config.get("label", "")))
        if button_config.get("target") == "terminal":
            button.connect("clicked", lambda _button: self._send_to_terminal(button_config["command"]))
        else:
            button.connect("clicked", lambda _button: self._launch(button_config["command"]))

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)

        icon_name = button_config.get("icon")
        if icon_name:
            image = Gtk.Image.new_from_icon_name(str(icon_name), Gtk.IconSize.DIALOG)
            image.set_pixel_size(24)
            inner.pack_start(image, False, False, 0)

        label = Gtk.Label(label=str(button_config.get("label", "")))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(10)
        label.set_justify(Gtk.Justification.CENTER)
        inner.pack_start(label, False, False, 0)

        button.add(inner)
        return button

    def _build_session_buttons(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("session-row")
        row.set_homogeneous(True)

        for button_config in self.config.get("session_buttons", []):
            row.pack_start(self._build_session_button(button_config), True, True, 0)

        return row

    def _build_session_button(self, button_config: dict[str, Any]) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("session-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(str(button_config.get("label", "")))
        if button_config.get("action") == "reload":
            button.connect("clicked", lambda _button: self._reload())
        else:
            button.connect("clicked", lambda _button: self._launch(button_config["command"]))

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)

        icon_name = button_config.get("icon")
        if icon_name:
            image = Gtk.Image.new_from_icon_name(str(icon_name), Gtk.IconSize.BUTTON)
            inner.pack_start(image, False, False, 0)

        label = Gtk.Label(label=str(button_config.get("label", "")))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(9)
        inner.pack_start(label, False, False, 0)

        button.add(inner)
        return button

    def _build_terminal(self) -> Gtk.Widget:
        terminal_config = self.config.get("terminal", {})
        terminal = Vte.Terminal()
        self.terminal = terminal
        terminal.set_can_focus(True)
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        terminal.set_scrollback_lines(int(terminal_config.get("scrollback_lines", 10000)))
        terminal.connect("button-press-event", self._on_terminal_button_press)

        font = terminal_config.get("font")
        if font:
            terminal.set_font(Pango.FontDescription(str(font)))

        argv = normalize_argv(terminal_config.get("command")) or [os.environ.get("SHELL", "/bin/bash")]
        cwd = terminal_config.get("working_directory") or os.environ.get("HOME")
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            str(cwd) if cwd else None,
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
            None,
        )
        return terminal

    def _build_resize_handle(self) -> Gtk.Widget:
        handle = Gtk.EventBox()
        handle.get_style_context().add_class("resize-handle")
        handle.set_size_request(6, -1)
        handle.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        handle.connect("realize", self._on_resize_handle_realize)
        handle.connect("button-press-event", self._on_resize_press)
        handle.connect("button-release-event", self._on_resize_release)
        handle.connect("motion-notify-event", self._on_resize_motion)
        return handle

    def _on_realize(self, _window: Gtk.Window) -> None:
        self._apply_panel_geometry()
        self.panel_geometry_applied = True
        self._apply_strut()
        GLib.idle_add(self._focus_terminal)
        if self.tray_host is not None:
            self.tray_host.start()

    def _on_configure(self, _window: Gtk.Window, event: Gdk.EventConfigure) -> bool:
        if self.panel_geometry_applied and event.width != self.panel_width:
            self.panel_width = max(120, int(event.width))
            self._apply_strut()
        return False

    def _on_destroy(self, _window: Gtk.Window) -> None:
        if self.tray_host is not None:
            self.tray_host.stop()
        Gtk.main_quit()

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and event.keyval == Gdk.KEY_q:
            self.destroy()
            return True
        return False

    def _on_terminal_button_press(self, terminal: Vte.Terminal, event: Gdk.EventButton) -> bool:
        self.present_with_time(event.time)
        terminal.grab_focus()
        return False

    def _focus_terminal(self) -> bool:
        if self.terminal is not None:
            self.terminal.grab_focus()
        return False

    def _on_resize_handle_realize(self, handle: Gtk.Widget) -> None:
        gdk_window = handle.get_window()
        display = Gdk.Display.get_default()
        if gdk_window is not None and display is not None:
            cursor = Gdk.Cursor.new_from_name(display, "ew-resize")
            if cursor is not None:
                gdk_window.set_cursor(cursor)

    def _on_resize_press(self, _handle: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.resize_drag = (self.panel_width, event.x_root)
        return True

    def _on_resize_release(self, _handle: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self.resize_drag = None
        return True

    def _on_resize_motion(self, _handle: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self.resize_drag is None:
            return False

        start_width, start_x = self.resize_drag
        delta = event.x_root - start_x
        if self.config["panel"].get("side", "left") == "right":
            delta = -delta

        self._set_panel_width(start_width + int(delta))
        return True

    def _update_clock(self) -> bool:
        now = datetime.now()
        clock = self.config.get("clock", {})
        self.time_label.set_text(now.strftime(str(clock.get("time_format", "%H:%M:%S"))))
        self.date_label.set_text(now.strftime(str(clock.get("date_format", "%A %d %B %Y"))))
        return True

    def _update_status_items(self) -> bool:
        for item, label in self.status_labels:
            item_type = item.get("type")
            if item_type == "volume":
                label.set_text(read_volume_status())
            elif item_type == "wifi":
                label.set_text(read_wifi_status())
        return True

    def _launch(self, command: str | list[str]) -> None:
        try:
            if isinstance(command, str):
                subprocess.Popen(command, shell=True, start_new_session=True)
            else:
                subprocess.Popen(command, start_new_session=True)
        except Exception as exc:
            self._show_error(f"Comando non avviato: {command}\n{exc}")

    def _send_to_terminal(self, command: str | list[str]) -> None:
        if self.terminal is None:
            self._show_error("Terminale non disponibile.")
            return

        command_line = command_to_shell_line(command)
        self.present()
        self.terminal.grab_focus()
        self.terminal.feed_child((command_line + "\n").encode("utf-8"))

    def _reload(self) -> None:
        if self.tray_host is not None:
            self.tray_host.stop()
        executable = sys.executable or "python3"
        os.execvp(executable, [executable, "-m", "ds_bar", *sys.argv[1:]])

    def _show_error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message,
        )
        dialog.run()
        dialog.destroy()

    def _apply_panel_geometry(self) -> None:
        panel = self.config["panel"]
        width = self.panel_width
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        height_config = panel.get("height", "screen")
        height = geometry.height if height_config == "screen" else int(height_config)
        side = panel.get("side", "left")
        x = geometry.x if side == "left" else geometry.x + geometry.width - width
        y = geometry.y

        self.set_default_size(width, height)
        self.resize(width, height)
        self.move(x, y)

    def _set_panel_width(self, width: int) -> None:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        self.panel_width = max(120, min(int(width), geometry.width))
        self._apply_panel_geometry()
        self._apply_strut()

    def _apply_strut(self) -> None:
        if not self.config.get("panel", {}).get("reserve_space", True):
            return
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return

        gdk_window = self.get_window()
        if gdk_window is None:
            return

        try:
            xid = GdkX11.X11Window.get_xid(gdk_window)
            from Xlib import X, display

            xdisplay = display.Display()
            xwindow = xdisplay.create_resource_object("window", xid)
            cardinal = xdisplay.intern_atom("CARDINAL")
            strut = xdisplay.intern_atom("_NET_WM_STRUT")
            strut_partial = xdisplay.intern_atom("_NET_WM_STRUT_PARTIAL")
            side = self.config["panel"].get("side", "left")
            width = self.panel_width

            display_gdk = Gdk.Display.get_default()
            monitor = display_gdk.get_primary_monitor() or display_gdk.get_monitor(0)
            geometry = monitor.get_geometry()
            start_y = geometry.y
            end_y = geometry.y + geometry.height - 1

            if side == "left":
                values = [width, 0, 0, 0, start_y, end_y, 0, 0, 0, 0, 0, 0]
            else:
                values = [0, width, 0, 0, 0, 0, start_y, end_y, 0, 0, 0, 0]

            xwindow.change_property(strut, cardinal, 32, values[:4])
            xwindow.change_property(strut_partial, cardinal, 32, values)
            xdisplay.flush()
        except Exception as exc:
            print(f"ds-bar: impossibile impostare lo spazio dock: {exc}", file=sys.stderr)

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def normalize_argv(command: str | list[str] | None) -> list[str] | None:
    if command is None:
        return None
    if isinstance(command, str):
        return shlex.split(command)
    return list(command)


def command_to_shell_line(command: str | list[str]) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(part) for part in command)


def run_text_command(argv: list[str], timeout: float = 2.0) -> str:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def read_volume_status() -> str:
    mute = run_text_command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
    if "yes" in mute.lower():
        return "Mute"

    volume = run_text_command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    match = re.search(r"(\d+)%", volume)
    return f"{match.group(1)}%" if match else "Vol"


def read_wifi_status() -> str:
    output = run_text_command(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"])
    for line in output.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == "yes":
            ssid = parts[1] or "Wi-Fi"
            signal = parts[2]
            return f"{ssid} {signal}%"
    return "Wi-Fi"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ds-bar side toolbar")
    parser.add_argument("--config", type=Path, default=None, help="percorso del file config JSON")
    parser.add_argument(
        "--print-default-config",
        action="store_true",
        help="stampa la configurazione predefinita ed esce",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_default_config:
        print(json.dumps(default_config(), indent=2))
        return 0

    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError, ConfigError) as exc:
        print(f"ds-bar: configurazione non valida: {exc}", file=sys.stderr)
        return 2

    DsBarWindow(config, args.config)
    Gtk.main()
    return 0
