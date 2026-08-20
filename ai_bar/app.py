from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, GdkX11, GLib, Gtk, Pango, Vte

try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck
except Exception:  # pragma: no cover - exercised only on systems without Wnck.
    Wnck = None

try:
    gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2
except Exception:  # pragma: no cover - exercised only on systems without WebKit2.
    WebKit2 = None

try:
    from Xlib import X, XK, display as xlib_display
    from Xlib.ext import record
    from Xlib.protocol import rq
except Exception:  # pragma: no cover - exercised only on systems without python-xlib.
    X = None
    XK = None
    xlib_display = None
    record = None
    rq = None

from .config import ConfigError, default_config, default_config_path, load_config
from .xapp_tray import XAppStatusIconHost
from .xembed_tray import XEmbedTrayHost


CLOCK_VERTICAL_SPACING = 2
CLOCK_HORIZONTAL_SPACING = 8
WINDOW_LIST_REFRESH_SECONDS = 2
PANEL_ANIMATION_INTERVAL_MS = 16
PANEL_ANIMATION_MIN_STEP = 18
VOLUME_UPDATE_DELAY_MS = 120
LAUNCH_MAXIMIZE_INTERVAL_MS = 100
LAUNCH_MAXIMIZE_ATTEMPTS = 50
EMBED_POLL_INTERVAL_MS = 150
EMBED_POLL_ATTEMPTS = 60
WEBVIEW_ZOOM_STEP = 0.1
TERMINAL_FOREGROUND = "#f2f2ee"
TERMINAL_BACKGROUND = "#151819"
TERMINAL_PALETTE = (
    "#3b4042",
    "#ff6b6b",
    "#8bd450",
    "#f4bf75",
    "#6cb6ff",
    "#d38aea",
    "#67d8ef",
    "#d8dee9",
    "#687176",
    "#ff8787",
    "#a6e22e",
    "#ffd866",
    "#a5d6ff",
    "#e5b2ff",
    "#8be9fd",
    "#ffffff",
)


def clock_labels_fit_inline(available_width: int, time_width: int, date_width: int, spacing: int) -> bool:
    return time_width + date_width + spacing <= available_width


def panel_x_for_state(side: str, screen_x: int, screen_width: int, panel_width: int, hidden: bool) -> int:
    if side == "right":
        return screen_x + screen_width if hidden else screen_x + screen_width - panel_width
    return screen_x - panel_width if hidden else screen_x


def panel_vertical_span(workarea: Any, height_config: Any) -> tuple[int, int]:
    # Top edge and height come from the work area rather than the raw monitor
    # geometry, so the panel stops short of docks and panels that already
    # reserved space for themselves instead of covering them.
    height = workarea.height if height_config == "screen" else int(height_config)
    return workarea.y, height


def panel_animation_step(distance: int) -> int:
    # Clamping to the remaining distance is what keeps the animation from
    # oscillating: without it the minimum step overshoots the target whenever
    # less than PANEL_ANIMATION_MIN_STEP is left to travel.
    return min(abs(distance), max(PANEL_ANIMATION_MIN_STEP, abs(distance) // 3))


def clean_window_title(title: str) -> str:
    return " ".join(title.split()) or "Finestra"


def terminal_tab_label(command: str | list[str] | None) -> str:
    if not command:
        return "Terminale"
    first = shlex.split(command)[0] if isinstance(command, str) else command[0]
    name = Path(first).name
    return {"ds-code": "DS Code"}.get(name, name.capitalize())


def webkit_cookie_storage_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "ai-bar" / "webkit" / "cookies.sqlite"
    return Path.home() / ".local" / "share" / "ai-bar" / "webkit" / "cookies.sqlite"


def configuration_assistant_command(config_path: Path) -> list[str]:
    return [
        sys.executable or "python3",
        "-m",
        "ai_bar.config_assistant",
        "--config",
        str(config_path.resolve()),
    ]


def find_window_by_xid(windows: list[Any], xid: int) -> Any | None:
    for window in windows:
        if int(window.get_xid()) == xid:
            return window
    return None


def centered_position(area: Any, width: int, height: int) -> tuple[int, int]:
    # Top left corner that centres a window of this size inside the area. A
    # window larger than the area rests on the origin rather than taking
    # negative coordinates, which would push it off screen.
    x = area.x + max(0, (area.width - width) // 2)
    y = area.y + max(0, (area.height - height) // 2)
    return x, y


def place_window(window: Any, area: Any | None, maximize: bool) -> None:
    # Moving comes before maximizing because the window manager maximizes onto
    # whichever monitor the window currently sits on: doing it the other way
    # round would expand the window on the monitor we are trying to leave.
    if area is not None and Wnck is not None:
        try:
            window.unmaximize()
            if maximize:
                # The exact spot does not matter, it is about to be maximized;
                # only the monitor does.
                x, y = area.x, area.y
            else:
                _x, _y, width, height = window.get_geometry()
                x, y = centered_position(area, width, height)
            window.set_geometry(
                Wnck.WindowGravity.CURRENT,
                Wnck.WindowMoveResizeMask.X | Wnck.WindowMoveResizeMask.Y,
                x, y, -1, -1)
        except Exception as exc:
            print(f"ai-bar: finestra non spostata: {exc}", file=sys.stderr)
    if maximize:
        window.maximize()


def maximize_launched_window(
    windows: list[Any],
    existing_xids: set[int],
    active_window: Any | None,
    previous_active_xid: int | None,
    own_xid: int | None,
    area: Any | None = None,
    maximize: bool = True,
) -> bool:
    for window in reversed(windows):
        xid = int(window.get_xid())
        if xid != own_xid and xid not in existing_xids:
            place_window(window, area, maximize)
            return True

    if active_window is not None:
        xid = int(active_window.get_xid())
        if xid != own_xid and xid != previous_active_xid:
            place_window(active_window, area, maximize)
            return True

    return False


@dataclass(frozen=True)
class WindowInfo:
    xid: int
    title: str
    active: bool
    icon: Any | None = None


@dataclass(frozen=True)
class VolumeState:
    percent: int
    muted: bool


class X11SuperToggle:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.display: Any = None
        self.keycodes: set[int] = set()
        self.context: Any = None
        self.thread: threading.Thread | None = None
        self.super_keys_down: set[int] = set()
        self.super_interrupted = False

    def start(self) -> bool:
        if X is None or XK is None or xlib_display is None or record is None or rq is None:
            print("ai-bar: python-xlib non disponibile, toggle Super disattivato.", file=sys.stderr)
            return False

        try:
            self.display = xlib_display.Display()
            for key_name in ("Super_L", "Super_R"):
                keycode = self.display.keysym_to_keycode(XK.string_to_keysym(key_name))
                if keycode:
                    self.keycodes.add(int(keycode))

            if not self.keycodes:
                self.display.close()
                self.display = None
                return False

            self.context = self.display.record_create_context(
                0,
                [record.AllClients],
                [
                    {
                        "core_requests": (0, 0),
                        "core_replies": (0, 0),
                        "ext_requests": (0, 0, 0, 0),
                        "ext_replies": (0, 0, 0, 0),
                        "delivered_events": (0, 0),
                        "device_events": (X.KeyPress, X.KeyRelease),
                        "errors": (0, 0),
                        "client_started": False,
                        "client_died": False,
                    }
                ],
            )
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True
        except Exception as exc:
            print(f"ai-bar: toggle Super disattivato: {exc}", file=sys.stderr)
            self.stop()
            return False

    def stop(self) -> None:
        if self.display is not None and self.context is not None:
            try:
                self.display.record_disable_context(self.context)
            except Exception:
                pass

        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1)
        self.thread = None

        self.keycodes.clear()
        self.super_keys_down.clear()
        self.super_interrupted = False
        self.context = None
        self.display = None

    def _run(self) -> None:
        try:
            if self.display is not None and self.context is not None:
                self.display.record_enable_context(self.context, self._handle_record_reply)
        finally:
            if self.display is not None and self.context is not None:
                try:
                    self.display.record_free_context(self.context)
                except Exception:
                    pass
            try:
                self.display.close()
            except Exception:
                pass

    def _handle_record_reply(self, reply: Any) -> None:
        if reply.category != record.FromServer or reply.client_swapped:
            return

        data = reply.data
        while data:
            event, data = rq.EventField(None).parse_binary_value(
                data, self.display.display, None, None
            )
            self._handle_event(event)

    def _handle_event(self, event: Any) -> None:
        keycode = int(event.detail)
        if keycode in self.keycodes:
            if event.type == X.KeyPress:
                self.super_keys_down.add(keycode)
                return

            if event.type == X.KeyRelease:
                if len(self.super_keys_down) == 1 and not self.super_interrupted:
                    GLib.idle_add(self._emit_callback)
                self.super_keys_down.discard(keycode)
                if not self.super_keys_down:
                    self.super_interrupted = False
                return

        if event.type == X.KeyPress and self.super_keys_down:
            self.super_interrupted = True

    def _emit_callback(self) -> bool:
        self.callback()
        return False


CSS = """
#ai-bar {
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
button.window-button,
button.launcher-button,
button.session-button {
  background: #242829;
  border: 1px solid #303638;
  border-radius: 6px;
  color: #f2f2ee;
  padding: 6px;
}

button.status-button:hover,
button.window-button:hover,
button.launcher-button:hover,
button.session-button:hover {
  background: #2d3335;
  border-color: #4c8f72;
}

button.window-button.active-window {
  background: #314238;
  border-color: #63b68e;
}

button.window-button {
  min-height: 30px;
  padding: 4px 7px;
}

button.status-button {
  min-height: 30px;
  padding: 4px 7px;
}

.volume-control {
  background: #242829;
  border: 1px solid #303638;
  border-radius: 6px;
  padding: 3px 5px;
}

button.volume-mute-button,
button.volume-settings-button {
  background: transparent;
  border: 0;
  border-radius: 4px;
  padding: 3px;
}

button.volume-mute-button:hover,
button.volume-settings-button:hover {
  background: #2d3335;
}

scale.volume-slider {
  min-width: 100px;
}

.volume-percent {
  min-width: 38px;
}

button.launcher-button {
  min-height: 60px;
}

.status-area {
  margin-bottom: 2px;
}

.status-flow {
  margin-bottom: 0;
}

.window-flow {
  margin-top: 2px;
}

.tray-icon-cell {
  background-color: #242829;
  border: 1px solid #303638;
  border-radius: 6px;
  min-height: 30px;
  min-width: 30px;
  padding: 3px;
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


class AiBarWindow(Gtk.Window):
    def __init__(self, config: dict[str, Any], config_path: Path | None = None) -> None:
        super().__init__(title="ai-bar")
        self.config = config
        self.config_path = config_path
        self.web_context: Any | None = None
        self.tray_host: XEmbedTrayHost | None = None
        self.xapp_tray_host: XAppStatusIconHost | None = None
        self.status_labels: list[tuple[dict[str, Any], Gtk.Label]] = []
        self.volume_controls: list[tuple[Gtk.Scale, Gtk.Label, Gtk.Image]] = []
        self.volume_percent = 0
        self.volume_muted = False
        self.volume_should_unmute = False
        self.volume_update_timeout_id: int | None = None
        self.terminal: Vte.Terminal | None = None
        self.terminals: dict[str, Vte.Terminal] = {}
        self.embedded: dict[str, Gtk.Widget] = {}
        self.terminal_notebook: Gtk.Notebook | None = None
        self.wnck_screen: Any = None
        self.window_flow: Gtk.FlowBox | None = None
        self.window_children: list[Gtk.FlowBoxChild] = []
        self.window_list_signature: tuple[tuple[int, str, bool], ...] = ()
        self.window_list_poll_id: int | None = None
        self.own_xid: int | None = None
        self.super_toggle: X11SuperToggle | None = None
        self.panel_width = int(self.config["panel"]["width"])
        self.panel_hidden = False
        self.panel_animation_id: int | None = None
        self.panel_geometry_applied = False
        self.monitor_warning_shown = False
        self.launch_monitor_warning_shown = False
        self.resize_drag: tuple[int, float] | None = None

        self._configure_webkit_cookie_persistence()

        panel = self.config["panel"]
        self.set_name("ai-bar")
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

    def _configure_webkit_cookie_persistence(self) -> None:
        if WebKit2 is None:
            return

        self.web_context = WebKit2.WebContext.get_default()
        cookie_manager = self.web_context.get_website_data_manager().get_cookie_manager()
        cookie_path = webkit_cookie_storage_path()
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_manager.set_persistent_storage(
            str(cookie_path),
            WebKit2.CookiePersistentStorage.SQLITE,
        )

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_border_width(10)

        content.pack_start(self._build_clock(), False, False, 0)
        content.pack_start(self._build_tray_row(), False, False, 0)

        for group in self.config.get("launcher_groups", []):
            content.pack_start(self._build_launcher_group(group), False, False, 0)

        self.terminal_notebook = Gtk.Notebook()
        self.terminal_notebook.get_style_context().add_class("terminal-wrap")
        self.terminal_notebook.set_show_tabs(False)
        self.terminal_notebook.set_scrollable(True)
        self.terminal_notebook.connect("switch-page", self._on_terminal_page_switched)
        initial_command = self.config.get("terminal", {}).get("command")
        initial_terminal = self._build_terminal(initial_command)
        initial_key = terminal_session_key(initial_command)
        self.terminals[initial_key] = initial_terminal
        self.terminal_notebook.append_page(
            initial_terminal,
            Gtk.Label(label=terminal_tab_label(initial_command)),
        )
        content.pack_start(self.terminal_notebook, True, True, 0)
        content.pack_start(self._build_session_buttons(), False, False, 0)

        resizable = bool(self.config["panel"].get("resizable", True))
        if resizable and self.config["panel"].get("side", "left") == "right":
            root.pack_start(self._build_resize_handle(), False, False, 0)

        root.pack_start(content, True, True, 0)

        if resizable and self.config["panel"].get("side", "left") == "left":
            root.pack_start(self._build_resize_handle(), False, False, 0)

        return root

    def _build_clock(self) -> Gtk.Widget:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=CLOCK_VERTICAL_SPACING)
        self.time_label = Gtk.Label()
        self.date_label = Gtk.Label()
        self.time_label.get_style_context().add_class("clock-time")
        self.date_label.get_style_context().add_class("clock-date")
        self.time_label.set_xalign(0.5)
        self.date_label.set_xalign(0.5)
        self.clock_box.pack_start(self.time_label, False, False, 0)
        self.clock_box.pack_start(self.date_label, False, False, 0)
        wrapper.pack_start(self.clock_box, False, False, 0)
        wrapper.connect("size-allocate", self._on_clock_size_allocate)

        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)
        return wrapper

    def _build_tray_row(self) -> Gtk.Widget:
        status_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        status_area.get_style_context().add_class("status-area")

        status_flow = Gtk.FlowBox()
        status_flow.get_style_context().add_class("status-flow")
        status_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        status_flow.set_min_children_per_line(1)
        status_flow.set_max_children_per_line(20)
        status_flow.set_column_spacing(6)
        status_flow.set_row_spacing(6)
        status_flow.set_direction(Gtk.TextDirection.LTR)

        for item in self.config.get("tray", {}).get("items", []):
            if item.get("type") == "volume":
                self._add_flow_child(status_flow, self._build_configuration_assistant_button())
            self._add_flow_child(status_flow, self._build_status_button(item))

        tray_flow = Gtk.FlowBox()
        tray_flow.get_style_context().add_class("status-flow")
        tray_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        tray_flow.set_min_children_per_line(1)
        tray_flow.set_max_children_per_line(20)
        tray_flow.set_column_spacing(6)
        tray_flow.set_row_spacing(6)
        tray_flow.set_direction(Gtk.TextDirection.LTR)

        icon_size = int(self.config.get("tray", {}).get("icon_size", 24))
        self.xapp_tray_host = XAppStatusIconHost(
            tray_flow,
            icon_size,
            str(self.config["panel"].get("side", "left")),
        )
        if self.config.get("tray", {}).get("xembed", True):
            self.tray_host = XEmbedTrayHost(tray_flow, icon_size)

        self.window_flow = Gtk.FlowBox()
        self.window_flow.get_style_context().add_class("window-flow")
        self.window_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.window_flow.set_min_children_per_line(1)
        self.window_flow.set_max_children_per_line(3)
        self.window_flow.set_column_spacing(6)
        self.window_flow.set_row_spacing(6)

        refresh = int(self.config.get("tray", {}).get("status_refresh_seconds", 5))
        if self.status_labels or self.volume_controls:
            self._update_status_items()
            GLib.timeout_add_seconds(max(1, refresh), self._update_status_items)

        status_area.pack_start(status_flow, False, False, 0)
        status_area.pack_start(tray_flow, False, False, 0)
        status_area.pack_start(self.window_flow, False, False, 0)
        return status_area

    def _add_flow_child(self, flow: Gtk.FlowBox, widget: Gtk.Widget) -> Gtk.FlowBoxChild:
        widget.set_direction(Gtk.TextDirection.LTR)
        child = Gtk.FlowBoxChild()
        child.add(widget)
        flow.insert(child, -1)
        return child

    def _build_status_button(self, item: dict[str, Any]) -> Gtk.Widget:
        if item.get("type") == "volume":
            return self._build_volume_control(item)

        button = Gtk.Button()
        button.get_style_context().add_class("status-button")
        button.set_tooltip_text(str(item.get("label", "")))
        button.set_relief(Gtk.ReliefStyle.NONE)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        icon_name = item.get("icon")
        if icon_name:
            image = Gtk.Image.new_from_icon_name(str(icon_name), Gtk.IconSize.BUTTON)
            inner.pack_start(image, False, False, 0)

        if not item.get("icon_only", False):
            label = Gtk.Label(label=str(item.get("label", "")))
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(14)
            inner.pack_start(label, False, False, 0)
            if item.get("type") == "wifi":
                self.status_labels.append((item, label))
        button.add(inner)

        command = item.get("command")
        if command:
            button.connect("clicked", lambda _button: self._launch(command))

        return button

    def _build_volume_control(self, item: dict[str, Any]) -> Gtk.Widget:
        control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        control.get_style_context().add_class("volume-control")
        control.set_hexpand(True)

        mute_button = Gtk.Button()
        mute_button.get_style_context().add_class("volume-mute-button")
        mute_button.set_relief(Gtk.ReliefStyle.NONE)
        mute_button.set_tooltip_text("Attiva o disattiva l'audio")
        mute_button.connect("clicked", self._on_volume_mute_clicked)
        icon = Gtk.Image.new_from_icon_name("audio-volume-muted-symbolic", Gtk.IconSize.BUTTON)
        mute_button.add(icon)
        control.pack_start(mute_button, False, False, 0)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.get_style_context().add_class("volume-slider")
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_tooltip_text("Volume")
        scale.connect("value-changed", self._on_volume_value_changed)
        control.pack_start(scale, True, True, 0)

        label = Gtk.Label(label="—")
        label.get_style_context().add_class("volume-percent")
        label.set_xalign(1)
        control.pack_start(label, False, False, 0)

        command = item.get("command")
        if command:
            settings_button = Gtk.Button()
            settings_button.get_style_context().add_class("volume-settings-button")
            settings_button.set_relief(Gtk.ReliefStyle.NONE)
            settings_button.set_tooltip_text("Impostazioni audio")
            settings_button.add(
                Gtk.Image.new_from_icon_name("preferences-system-symbolic", Gtk.IconSize.MENU)
            )
            settings_button.connect("clicked", lambda _button: self._launch(command))
            control.pack_start(settings_button, False, False, 0)

        self.volume_controls.append((scale, label, icon))
        return control

    def _build_configuration_assistant_button(self) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("status-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text("Configura AI-bar con un agente")
        button.add(Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.MENU))
        button.connect("clicked", lambda _button: self._open_configuration_assistant())
        return button

    def _open_configuration_assistant(self) -> None:
        config_path = self.config_path or default_config_path()
        command = configuration_assistant_command(config_path)
        key = terminal_session_key(command)
        previous_terminal = self.terminals.pop(key, None)
        if previous_terminal is not None:
            if self.terminal_notebook is not None:
                page = self.terminal_notebook.page_num(previous_terminal)
                if page >= 0:
                    self.terminal_notebook.remove_page(page)
            previous_terminal.destroy()
        self._switch_terminal(
            command,
            "Configura",
        )

    def _build_window_button(self, info: WindowInfo) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("window-button")
        if info.active:
            button.get_style_context().add_class("active-window")
        button.set_tooltip_text(info.title)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.connect("clicked", lambda _button: self._activate_window(info.xid))

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        if info.icon is not None:
            image = Gtk.Image.new_from_pixbuf(info.icon)
        else:
            image = Gtk.Image.new_from_icon_name("window-symbolic", Gtk.IconSize.MENU)
        inner.pack_start(image, False, False, 0)

        label = Gtk.Label(label=info.title)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(16)
        inner.pack_start(label, False, False, 0)
        button.add(inner)
        return button

    def _build_launcher_group(self, group: dict[str, Any]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        title = group.get("title")
        if title:
            label = Gtk.Label(label=str(title))
            label.set_xalign(0)
            label.get_style_context().add_class("section-title")
            box.pack_start(label, False, False, 0)

        buttons = list(group.get("buttons", []))
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(6)
        flow.set_row_spacing(6)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(1)
        flow.set_max_children_per_line(max(1, len(buttons)))

        for button_config in buttons:
            button = self._build_launcher_button(button_config)
            self._add_flow_child(flow, button)

        box.pack_start(flow, False, False, 0)
        return box

    def _build_launcher_button(self, button_config: dict[str, Any]) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("launcher-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_hexpand(True)
        button.set_halign(Gtk.Align.FILL)
        button.set_tooltip_text(str(button_config.get("label", "")))
        target = button_config.get("target")
        if target == "terminal":
            button.connect(
                "clicked",
                lambda _button: self._switch_terminal(
                    button_config["command"],
                    str(button_config.get("label", "")),
                ),
            )
        elif target == "window":
            button.connect(
                "clicked",
                lambda _button: self._switch_embedded_window(
                    button_config["command"],
                    str(button_config.get("label", "")),
                ),
            )
        elif target == "url":
            button.connect(
                "clicked",
                lambda _button: self._switch_webview(
                    str(button_config["url"]),
                    str(button_config.get("label", "")),
                ),
            )
        else:
            button.connect(
                "clicked",
                lambda _button: self._launch(
                    button_config["command"],
                    maximized=bool(button_config.get("maximized", False)),
                ),
            )

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_hexpand(True)

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
            command = button_config["command"]
            action = system_session_action(command)
            if action is not None:
                button.connect(
                    "clicked",
                    lambda _button: self._launch_session_action(action),
                )
            else:
                button.connect(
                    "clicked",
                    lambda _button: self._launch_session_command(command),
                )

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

    def _build_terminal(
        self,
        command: str | list[str] | None = None,
        width_px: int | None = None,
    ) -> Gtk.Widget:
        terminal_config = self.config.get("terminal", {})
        terminal = Vte.Terminal()
        self.terminal = terminal
        terminal.set_can_focus(True)
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        terminal.set_scrollback_lines(int(terminal_config.get("scrollback_lines", 10000)))
        terminal.connect("button-press-event", self._on_content_click)
        terminal.connect("button-press-event", self._on_terminal_button_press)
        terminal.connect("key-press-event", self._on_terminal_key_press)

        foreground = Gdk.RGBA()
        foreground.parse(TERMINAL_FOREGROUND)
        background = Gdk.RGBA()
        background.parse(TERMINAL_BACKGROUND)
        palette = []
        for color_value in TERMINAL_PALETTE:
            color = Gdk.RGBA()
            color.parse(color_value)
            palette.append(color)
        terminal.set_colors(foreground, background, palette)

        font = terminal_config.get("font")
        if font:
            terminal.set_font(Pango.FontDescription(str(font)))

        argv = terminal_argv(
            command if command is not None else terminal_config.get("command"),
            width_px=width_px,
        )
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
        gdk_window = self.get_window()
        if gdk_window is not None:
            self.own_xid = GdkX11.X11Window.get_xid(gdk_window)
        self._apply_panel_geometry()
        self.panel_geometry_applied = True
        self._apply_strut()
        self._start_window_list()
        self.super_toggle = X11SuperToggle(self._toggle_panel_visibility)
        self.super_toggle.start()
        GLib.idle_add(self._focus_terminal)
        if self.xapp_tray_host is not None:
            self.xapp_tray_host.start()
        if self.tray_host is not None:
            self.tray_host.start()

    def _on_configure(self, _window: Gtk.Window, event: Gdk.EventConfigure) -> bool:
        if self.panel_geometry_applied and event.width != self.panel_width:
            self.panel_width = max(120, int(event.width))
            self._apply_strut()
        return False

    def _on_destroy(self, _window: Gtk.Window) -> None:
        if self.volume_update_timeout_id is not None:
            GLib.source_remove(self.volume_update_timeout_id)
            self.volume_update_timeout_id = None
        if self.panel_animation_id is not None:
            GLib.source_remove(self.panel_animation_id)
            self.panel_animation_id = None
        if self.window_list_poll_id is not None:
            GLib.source_remove(self.window_list_poll_id)
            self.window_list_poll_id = None
        if self.super_toggle is not None:
            self.super_toggle.stop()
        if self.xapp_tray_host is not None:
            self.xapp_tray_host.stop()
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
        if event.button == 3:
            self._build_terminal_menu(terminal).popup_at_pointer(event)
            return True
        return False

    def _on_terminal_key_press(self, terminal: Vte.Terminal, event: Gdk.EventKey) -> bool:
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and shift and event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
            terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if ctrl and shift and event.keyval in (Gdk.KEY_v, Gdk.KEY_V):
            terminal.paste_clipboard()
            return True
        if ctrl and event.keyval == Gdk.KEY_Insert:
            terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if shift and event.keyval == Gdk.KEY_Insert:
            terminal.paste_clipboard()
            return True
        return False

    def _on_webview_key_press(self, webview: Any, event: Gdk.EventKey) -> bool:
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if not ctrl:
            return False

        if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._change_webview_zoom(webview, WEBVIEW_ZOOM_STEP)
            return True
        if event.keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._change_webview_zoom(webview, -WEBVIEW_ZOOM_STEP)
            return True
        return False

    def _change_webview_zoom(self, webview: Any, delta: float) -> None:
        current_zoom = float(webview.get_zoom_level())
        webview.set_zoom_level(max(0.25, min(3.0, current_zoom + delta)))

    def _webview_acceleration_policy(self) -> Any:
        # Il default e' "never": su driver dove l'allocazione del buffer GBM
        # fallisce (NVIDIA proprietario) WebKit non ripiega da solo e la scheda
        # resta semplicemente vuota, con "Failed to create GBM buffer" nel log
        # di sessione e nulla nell'interfaccia. Il costo del rendering software
        # su una vista larga quanto un pannello e' modesto, mentre quel guasto
        # e' invisibile e totale. Chi ha una scheda a posto rimette il
        # comportamento originale di WebKit con "on-demand".
        wanted = self.config.get("webview", {}).get("hardware_acceleration", "never")
        policies = {
            "never": WebKit2.HardwareAccelerationPolicy.NEVER,
            "on-demand": WebKit2.HardwareAccelerationPolicy.ON_DEMAND,
            "always": WebKit2.HardwareAccelerationPolicy.ALWAYS,
        }
        return policies.get(wanted, WebKit2.HardwareAccelerationPolicy.NEVER)

    def _on_content_click(self, widget: Gtk.Widget, _event: Gdk.EventButton) -> bool:
        # Il pannello e' una finestra di tipo DOCK, e i window manager non danno
        # il focus da tastiera a un dock quando ci si clicca dentro: il mouse
        # arriva (i pulsanti reagiscono) ma i campi di testo restano muti.
        # Chiederlo esplicitamente al primo clic ricrea il comportamento di una
        # finestra normale. Si ritorna False perche' il clic deve comunque
        # arrivare al contenuto.
        if not self.is_active():
            self.present()
            widget.grab_focus()
        return False

    def _build_terminal_menu(self, terminal: Vte.Terminal) -> Gtk.Menu:
        menu = Gtk.Menu()
        copy_item = Gtk.MenuItem(label="Copia")
        copy_item.connect("activate", lambda _item: terminal.copy_clipboard_format(Vte.Format.TEXT))
        paste_item = Gtk.MenuItem(label="Incolla")
        paste_item.connect("activate", lambda _item: terminal.paste_clipboard())
        menu.append(copy_item)
        menu.append(paste_item)
        menu.show_all()
        return menu

    def _focus_terminal(self) -> bool:
        if self.terminal is not None:
            self.terminal.grab_focus()
        return False

    def _on_terminal_page_switched(
        self,
        _notebook: Gtk.Notebook,
        terminal: Gtk.Widget,
        _page: int,
    ) -> None:
        if isinstance(terminal, Vte.Terminal):
            self.terminal = terminal

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
        self.clock_box.queue_resize()
        return True

    def _on_clock_size_allocate(self, _box: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        _time_min_width, time_width = self.time_label.get_preferred_width()
        _date_min_width, date_width = self.date_label.get_preferred_width()
        inline = clock_labels_fit_inline(
            allocation.width,
            time_width,
            date_width,
            CLOCK_HORIZONTAL_SPACING,
        )

        self.clock_box.set_orientation(Gtk.Orientation.HORIZONTAL if inline else Gtk.Orientation.VERTICAL)
        self.clock_box.set_spacing(CLOCK_HORIZONTAL_SPACING if inline else CLOCK_VERTICAL_SPACING)
        self.clock_box.set_halign(Gtk.Align.CENTER if inline else Gtk.Align.FILL)

    def _update_status_items(self) -> bool:
        for item, label in self.status_labels:
            item_type = item.get("type")
            if item_type == "wifi":
                label.set_text(read_wifi_status())

        if self.volume_controls:
            state = read_volume_state()
            if state is None:
                for _scale, label, icon in self.volume_controls:
                    label.set_text("—")
                    icon.set_from_icon_name("audio-volume-muted-symbolic", Gtk.IconSize.BUTTON)
            else:
                self._show_volume_state(state, update_scale=True)
        return True

    def _show_volume_state(self, state: VolumeState, update_scale: bool) -> None:
        self.volume_percent = state.percent
        self.volume_muted = state.muted
        icon_name = volume_icon_name(state.percent, state.muted)
        text = "Mute" if state.muted else f"{state.percent}%"

        for scale, label, icon in self.volume_controls:
            if update_scale:
                scale.handler_block_by_func(self._on_volume_value_changed)
                scale.set_value(min(state.percent, 100))
                scale.handler_unblock_by_func(self._on_volume_value_changed)
            label.set_text(text)
            icon.set_from_icon_name(icon_name, Gtk.IconSize.BUTTON)

    def _on_volume_value_changed(self, scale: Gtk.Scale) -> None:
        percent = round(scale.get_value())
        self.volume_should_unmute = self.volume_should_unmute or self.volume_muted
        self._show_volume_state(VolumeState(percent=percent, muted=False), update_scale=False)

        if self.volume_update_timeout_id is not None:
            GLib.source_remove(self.volume_update_timeout_id)
        self.volume_update_timeout_id = GLib.timeout_add(
            VOLUME_UPDATE_DELAY_MS,
            self._apply_volume_change,
            percent,
        )

    def _apply_volume_change(self, percent: int) -> bool:
        self.volume_update_timeout_id = None
        set_system_volume(percent)
        if self.volume_should_unmute:
            set_system_muted(False)
            self.volume_should_unmute = False
        return False

    def _on_volume_mute_clicked(self, _button: Gtk.Button) -> None:
        muted = not self.volume_muted
        if set_system_muted(muted):
            self._show_volume_state(
                VolumeState(percent=self.volume_percent, muted=muted),
                update_scale=False,
            )

    def _start_window_list(self) -> None:
        if self.window_flow is None:
            return

        if Wnck is None:
            print("ai-bar: libwnck non disponibile, elenco finestre disattivato.", file=sys.stderr)
            return

        try:
            Wnck.set_client_type(Wnck.ClientType.PAGER)
            self.wnck_screen = Wnck.Screen.get_default()
            if self.wnck_screen is None:
                return
        except Exception as exc:
            print(f"ai-bar: elenco finestre disattivato: {exc}", file=sys.stderr)
            return

        self._update_window_list()
        self.window_list_poll_id = GLib.timeout_add_seconds(WINDOW_LIST_REFRESH_SECONDS, self._update_window_list)

    def _update_window_list(self) -> bool:
        if self.window_flow is None or self.wnck_screen is None:
            return False

        try:
            self.wnck_screen.force_update()
            active_workspace = self.wnck_screen.get_active_workspace()
            active_window = self.wnck_screen.get_active_window()
            windows = []

            for window in self.wnck_screen.get_windows_stacked():
                xid = int(window.get_xid())
                if xid == self.own_xid or window.is_skip_tasklist():
                    continue
                if active_workspace is not None and not window.is_pinned() and not window.is_on_workspace(active_workspace):
                    continue

                title = clean_window_title(window.get_name() or "")
                windows.append(
                    WindowInfo(
                        xid=xid,
                        title=title,
                        active=window == active_window,
                        icon=window.get_mini_icon(),
                    )
                )
        except Exception as exc:
            print(f"ai-bar: elenco finestre non aggiornato: {exc}", file=sys.stderr)
            return True

        signature = tuple((window.xid, window.title, window.active) for window in windows)
        if signature == self.window_list_signature:
            return True

        for child in self.window_children:
            child.destroy()
        self.window_children.clear()

        for window in windows:
            child = self._add_flow_child(self.window_flow, self._build_window_button(window))
            self.window_children.append(child)

        self.window_list_signature = signature
        self.window_flow.show_all()
        return True

    def _activate_window(self, xid: int) -> None:
        if self.wnck_screen is None:
            return
        window = find_window_by_xid(self.wnck_screen.get_windows_stacked(), xid)
        if window is None:
            return
        try:
            window.activate(Gtk.get_current_event_time())
        except Exception as exc:
            print(f"ai-bar: finestra non attivata {xid}: {exc}", file=sys.stderr)

    def _launch(self, command: str | list[str], maximized: bool = False) -> None:
        existing_xids: set[int] | None = None
        previous_active_xid: int | None = None
        # A window that is not maximized still has to be followed when a launch
        # monitor is set, otherwise it opens behind the panel just the same.
        launch_area = self._launch_area()
        if (maximized or launch_area is not None) and self.wnck_screen is not None:
            try:
                self.wnck_screen.force_update()
                existing_xids = {
                    int(window.get_xid()) for window in self.wnck_screen.get_windows_stacked()
                }
                active_window = self.wnck_screen.get_active_window()
                if active_window is not None:
                    previous_active_xid = int(active_window.get_xid())
            except Exception as exc:
                print(f"ai-bar: stato finestre non disponibile: {exc}", file=sys.stderr)

        try:
            if isinstance(command, str):
                subprocess.Popen(command, shell=True, start_new_session=True)
            else:
                subprocess.Popen(command, start_new_session=True)
        except Exception as exc:
            self._show_error(f"Comando non avviato: {command}\n{exc}")
            return

        if existing_xids is not None:
            attempts_remaining = LAUNCH_MAXIMIZE_ATTEMPTS

            def maximize_when_ready() -> bool:
                nonlocal attempts_remaining
                try:
                    self.wnck_screen.force_update()
                    if maximize_launched_window(
                        self.wnck_screen.get_windows_stacked(),
                        existing_xids,
                        self.wnck_screen.get_active_window(),
                        previous_active_xid,
                        self.own_xid,
                        launch_area,
                        maximized,
                    ):
                        return False
                except Exception as exc:
                    print(f"ai-bar: finestra non sistemata: {exc}", file=sys.stderr)
                    return False

                attempts_remaining -= 1
                return attempts_remaining > 0

            GLib.timeout_add(LAUNCH_MAXIMIZE_INTERVAL_MS, maximize_when_ready)

    def _launch_session_command(self, command: str | list[str]) -> None:
        threading.Thread(
            target=self._run_session_command,
            args=(command,),
            daemon=True,
        ).start()

    def _run_session_command(self, command: str | list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                shell=isinstance(command, str),
                capture_output=True,
                text=True,
                start_new_session=True,
            )
        except Exception as exc:
            GLib.idle_add(self._show_error, f"Comando non avviato: {command}\n{exc}")
            return

        if completed.returncode != 0:
            message = (
                f"Comando non riuscito ({completed.returncode}): "
                f"{command_to_shell_line(command)}"
            )
            detail = (completed.stderr or completed.stdout or "").strip()
            if detail:
                message += f"\n{detail}"
            GLib.idle_add(self._show_error, message)

    def _launch_session_action(self, action: str) -> None:
        threading.Thread(
            target=self._run_session_action,
            args=(action,),
            daemon=True,
        ).start()

    def _run_session_action(self, action: str) -> None:
        try:
            supervisor_pid = int(os.environ["AI_BAR_SESSION_SUPERVISOR_PID"])
            result_path = Path(os.environ["AI_BAR_SESSION_RESULT"])
            result_dir = Path(os.environ["AI_BAR_SESSION_RESULT_DIR"])
        except (KeyError, ValueError):
            self._run_session_command(["/usr/bin/systemctl", action])
            return

        if (
            os.getppid() != supervisor_pid
            or result_path.parent != result_dir
            or not result_path.name.startswith("ai-bar-session-result-")
        ):
            self._run_session_command(["/usr/bin/systemctl", action])
            return

        try:
            result_path.unlink(missing_ok=True)
            requested_signal = signal.SIGUSR1 if action == "reboot" else signal.SIGUSR2
            os.kill(supervisor_pid, requested_signal)

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not result_path.is_file():
                time.sleep(0.05)
            response = result_path.read_text(encoding="utf-8")
            result_path.unlink(missing_ok=True)
            status_text, _, detail = response.partition("\n")
            status = int(status_text)
        except Exception as exc:
            GLib.idle_add(
                self._show_error,
                f"Comando non avviato: systemctl {action}\n{exc}",
            )
            return

        if status != 0:
            message = f"Comando non riuscito ({status}): systemctl {action}"
            if detail.strip():
                message += f"\n{detail.strip()}"
            GLib.idle_add(self._show_error, message)

    def _switch_terminal(self, command: str | list[str], label: str | None = None) -> None:
        if self.terminal_notebook is None:
            self._show_error("Terminale non disponibile.")
            return

        key = terminal_session_key(command)
        terminal = self.terminals.get(key)
        if terminal is None:
            terminal = self._build_terminal(command, width_px=self.panel_width)
            self.terminals[key] = terminal
            page = self.terminal_notebook.append_page(
                terminal,
                Gtk.Label(label=label or terminal_tab_label(command)),
            )
        else:
            page = self.terminal_notebook.page_num(terminal)
        self.terminal = terminal
        terminal.show_all()
        self.terminal_notebook.set_current_page(page)
        self.present()
        terminal.grab_focus()

    def _switch_embedded_window(self, command: str | list[str], label: str | None = None) -> None:
        if self.terminal_notebook is None:
            self._show_error("Terminale non disponibile.")
            return
        if Wnck is None or self.wnck_screen is None:
            print("ai-bar: libwnck non disponibile, finestra aperta esternamente.", file=sys.stderr)
            self._launch(command)
            return

        key = "window:" + command_to_shell_line(command)
        widget = self.embedded.get(key)
        if widget is None:
            try:
                subprocess.Popen(command, start_new_session=True)
            except Exception as exc:
                self._show_error(f"Comando non avviato: {command}\n{exc}")
                return
            socket = Gtk.Socket()
            socket.set_hexpand(True)
            socket.set_vexpand(True)
            socket.connect("realize", self._embed_launched_window)
            widget = socket
            self.embedded[key] = socket
            page = self.terminal_notebook.append_page(
                socket,
                Gtk.Label(label=label or terminal_tab_label(command)),
            )
        else:
            page = self.terminal_notebook.page_num(widget)
        widget.show_all()
        self.terminal_notebook.set_current_page(page)
        self.present()

    def _embed_launched_window(self, socket: Gtk.Socket) -> None:
        if self.wnck_screen is None:
            return
        try:
            self.wnck_screen.force_update()
            existing_xids = {
                int(window.get_xid()) for window in self.wnck_screen.get_windows_stacked()
            }
        except Exception as exc:
            print(f"ai-bar: stato finestre non disponibile: {exc}", file=sys.stderr)
            return
        attempts_remaining = EMBED_POLL_ATTEMPTS

        def embed_when_ready() -> bool:
            nonlocal attempts_remaining
            try:
                self.wnck_screen.force_update()
                for window in self.wnck_screen.get_windows_stacked():
                    xid = int(window.get_xid())
                    if xid != self.own_xid and xid not in existing_xids and not window.is_skip_tasklist():
                        socket.add_id(xid)
                        return False
            except Exception as exc:
                print(f"ai-bar: finestra non incorporata: {exc}", file=sys.stderr)
                return False

            attempts_remaining -= 1
            return attempts_remaining > 0

        GLib.timeout_add(EMBED_POLL_INTERVAL_MS, embed_when_ready)

    def _switch_webview(self, url: str, label: str | None = None) -> None:
        if self.terminal_notebook is None:
            self._show_error("Terminale non disponibile.")
            return
        if WebKit2 is None:
            self._launch(["xdg-open", url])
            return

        key = "url:" + url
        widget = self.embedded.get(key)
        if widget is None:
            webview = WebKit2.WebView.new_with_context(self.web_context or WebKit2.WebContext.get_default())
            webview.set_hexpand(True)
            webview.set_vexpand(True)
            settings = webview.get_settings()
            settings.set_hardware_acceleration_policy(
                self._webview_acceleration_policy())
            webview.set_settings(settings)
            webview.connect("key-press-event", self._on_webview_key_press)
            webview.connect("button-press-event", self._on_content_click)
            webview.load_uri(url)
            widget = webview
            self.embedded[key] = webview
            page = self.terminal_notebook.append_page(
                webview,
                Gtk.Label(label=label or "Web"),
            )
        else:
            page = self.terminal_notebook.page_num(widget)
        widget.show_all()
        self.terminal_notebook.set_current_page(page)
        self.present()
        widget.grab_focus()

    def _reload(self) -> None:
        if self.xapp_tray_host is not None:
            self.xapp_tray_host.stop()
        if self.tray_host is not None:
            self.tray_host.stop()
        executable = sys.executable or "python3"
        os.execvp(executable, [executable, "-m", "ai_bar", *sys.argv[1:]])

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
        geometry = self._monitor_geometry()
        self.panel_width = min(self.panel_width, geometry.width)
        width = self.panel_width
        y, height = panel_vertical_span(
            self._monitor_workarea(),
            panel.get("height", "screen"),
        )
        side = panel.get("side", "left")
        x = panel_x_for_state(side, geometry.x, geometry.width, width, self.panel_hidden)

        self.set_default_size(width, height)
        self.resize(width, height)
        self.move(x, y)

    def _set_panel_width(self, width: int) -> None:
        geometry = self._monitor_geometry()
        self.panel_width = max(120, min(int(width), geometry.width))
        self._apply_panel_geometry()
        self._apply_strut()

    def _toggle_panel_visibility(self) -> None:
        if self.panel_animation_id is not None:
            GLib.source_remove(self.panel_animation_id)
            self.panel_animation_id = None

        self.panel_hidden = not self.panel_hidden
        if self.panel_hidden:
            self._clear_strut()
        else:
            self.present()

        target_x = self._target_panel_x()
        self.panel_animation_id = GLib.timeout_add(PANEL_ANIMATION_INTERVAL_MS, self._animate_panel_to, target_x)

    def _target_panel_x(self) -> int:
        geometry = self._monitor_geometry()
        return panel_x_for_state(
            self.config["panel"].get("side", "left"),
            geometry.x,
            geometry.width,
            self.panel_width,
            self.panel_hidden,
        )

    def _animate_panel_to(self, target_x: int) -> bool:
        current_x, _current_y = self.get_position()
        geometry = self._monitor_workarea()
        distance = target_x - current_x
        if abs(distance) <= 3:
            self.move(target_x, geometry.y)
            self.panel_animation_id = None
            if not self.panel_hidden:
                self._apply_strut()
                GLib.idle_add(self._focus_terminal)
            return False

        step = panel_animation_step(distance)
        self.move(current_x + (step if distance > 0 else -step), geometry.y)
        return True

    def _monitor_geometry(self) -> Gdk.Rectangle:
        return self._resolve_monitor().get_geometry()

    def _monitor_workarea(self) -> Gdk.Rectangle:
        monitor = self._resolve_monitor()
        try:
            return monitor.get_workarea()
        except Exception:
            return monitor.get_geometry()

    def _find_monitor(self, wanted: Any) -> Any | None:
        # A monitor is named either by index or by connector name, the same
        # string xrandr prints, for example "DP-1". None when it matches
        # nothing, so callers can fall back instead of crashing at startup.
        display = Gdk.Display.get_default()
        if isinstance(wanted, int) and not isinstance(wanted, bool):
            return display.get_monitor(wanted)

        if isinstance(wanted, str):
            xrandr_output = run_text_command(["xrandr", "--query"])
            if xrandr_output:
                match = re.search(
                    rf"^({re.escape(wanted)})\s+connected(?:\s+primary)?\s+"
                    r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)\b",
                    xrandr_output,
                    re.MULTILINE,
                )
                if match is not None:
                    expected = tuple(int(value) for value in match.groups()[1:])
                    for index in range(display.get_n_monitors()):
                        monitor = display.get_monitor(index)
                        if monitor is None:
                            continue
                        geometry = monitor.get_geometry()
                        if (
                            geometry.width,
                            geometry.height,
                            geometry.x,
                            geometry.y,
                        ) == expected:
                            return monitor

        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            if monitor is not None and monitor.get_model() == str(wanted):
                return monitor
        return None

    def _primary_monitor(self) -> Any:
        display = Gdk.Display.get_default()
        return display.get_primary_monitor() or display.get_monitor(0)

    def _resolve_monitor(self) -> Any:
        wanted = self.config.get("panel", {}).get("monitor")
        if wanted is None:
            return self._primary_monitor()

        monitor = self._find_monitor(wanted)
        if monitor is not None:
            return monitor

        if not self.monitor_warning_shown:
            self.monitor_warning_shown = True
            print(f"ai-bar: monitor {wanted!r} non trovato, uso il primario.",
                  file=sys.stderr)
        return self._primary_monitor()

    def _launch_area(self) -> Any | None:
        # Where windows started from the panel should appear. Leaving them on
        # the panel monitor is a poor default when ai-bar covers it entirely:
        # the panel is kept above everything, so the new window opens hidden
        # behind it. None means "leave placement to the window manager".
        wanted = self.config.get("panel", {}).get("launch_monitor")
        if wanted is None:
            return None

        if wanted == "auto":
            monitor = self._primary_monitor()
        else:
            monitor = self._find_monitor(wanted)
            if monitor is None:
                if not self.launch_monitor_warning_shown:
                    self.launch_monitor_warning_shown = True
                    print(f"ai-bar: monitor di lancio {wanted!r} non trovato, "
                          "le finestre restano dove le mette il window manager.",
                          file=sys.stderr)
                return None
        if monitor is None:
            return None

        area = monitor.get_workarea()
        # A single monitor, or a panel already sitting on the target: moving
        # windows would solve nothing and would only surprise the user.
        panel_geometry = self._resolve_monitor().get_geometry()
        if area.x == panel_geometry.x and area.y == panel_geometry.y:
            return None
        return area

    def _monitor_geometry(self) -> Gdk.Rectangle:
        return self._resolve_monitor().get_geometry()

    def _apply_strut(self) -> None:
        if self.panel_hidden or not self.config.get("panel", {}).get("reserve_space", True):
            self._clear_strut()
            return
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return

        geometry = self._monitor_geometry()
        display = Gdk.Display.get_default()
        if display is None:
            return

        screen_start = None
        screen_end = None
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            if monitor is None:
                continue
            monitor_geometry = monitor.get_geometry()
            if screen_start is None or monitor_geometry.x < screen_start:
                screen_start = monitor_geometry.x
            monitor_end = monitor_geometry.x + monitor_geometry.width
            if screen_end is None or monitor_end > screen_end:
                screen_end = monitor_end

        start_y = geometry.y
        end_y = geometry.y + geometry.height - 1
        side = self.config["panel"].get("side", "left")
        width = self.panel_width
        if side == "left" and screen_start is not None and geometry.x > screen_start:
            self._clear_strut()
            return
        if side == "right" and screen_end is not None and geometry.x + geometry.width < screen_end:
            self._clear_strut()
            return

        if side == "left":
            values = [width, 0, 0, 0, start_y, end_y, 0, 0, 0, 0, 0, 0]
        else:
            values = [0, width, 0, 0, 0, 0, start_y, end_y, 0, 0, 0, 0]
        self._set_strut_values(values)

    def _clear_strut(self) -> None:
        self._set_strut_values([0] * 12, quiet=True)

    def _set_strut_values(self, values: list[int], quiet: bool = False) -> None:
        if X is None or xlib_display is None or os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return

        gdk_window = self.get_window()
        if gdk_window is None:
            return

        try:
            xid = GdkX11.X11Window.get_xid(gdk_window)
            xdisplay = xlib_display.Display()
            xwindow = xdisplay.create_resource_object("window", xid)
            cardinal = xdisplay.intern_atom("CARDINAL")
            strut = xdisplay.intern_atom("_NET_WM_STRUT")
            strut_partial = xdisplay.intern_atom("_NET_WM_STRUT_PARTIAL")
            xwindow.change_property(strut, cardinal, 32, values[:4])
            xwindow.change_property(strut_partial, cardinal, 32, values)
            xdisplay.flush()
        except Exception as exc:
            if not quiet:
                print(f"ai-bar: impossibile impostare lo spazio dock: {exc}", file=sys.stderr)

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def terminal_argv(command: str | list[str] | None, width_px: int | None = None) -> list[str]:
    shell = os.environ.get("SHELL", "/bin/bash")
    if command is None:
        argv = [shell]
    else:
        argv = [shell, "-lc", command_to_shell_line(command)]
    if width_px is not None:
        return ["env", f"AI_BAR_TERMINAL_WIDTH_PX={width_px}", *argv]
    return argv


def terminal_session_key(command: str | list[str] | None) -> str:
    if command is None:
        return "__shell__"
    return command_to_shell_line(command)


def command_to_shell_line(command: str | list[str]) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(part) for part in command)


def system_session_action(command: str | list[str]) -> str | None:
    if not isinstance(command, list):
        return None
    if (
        len(command) == 2
        and Path(command[0]).name == "systemctl"
        and command[1] in {"reboot", "poweroff"}
    ):
        return command[1]
    if (
        len(command) == 3
        and Path(command[0]).name == "pkexec"
        and Path(command[1]).name == "systemctl"
        and command[2] in {"reboot", "poweroff"}
    ):
        return command[2]
    return None


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


def volume_state_from_wpctl(output: str) -> VolumeState | None:
    match = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", output)
    if not match:
        return None
    return VolumeState(
        percent=round(float(match.group(1)) * 100),
        muted="muted" in output.lower(),
    )


def volume_state_from_pactl(volume_output: str, mute_output: str = "") -> VolumeState | None:
    matches = re.findall(r"(\d+)%", volume_output)
    if not matches:
        return None
    return VolumeState(
        percent=max(int(match) for match in matches),
        muted="yes" in mute_output.lower(),
    )


def volume_icon_name(percent: int, muted: bool) -> str:
    if muted or percent == 0:
        return "audio-volume-muted-symbolic"
    if percent < 34:
        return "audio-volume-low-symbolic"
    if percent < 67:
        return "audio-volume-medium-symbolic"
    return "audio-volume-high-symbolic"


def volume_status_from_wpctl(output: str) -> str:
    state = volume_state_from_wpctl(output)
    if state is None:
        return ""
    return "Mute" if state.muted else f"{state.percent}%"


def volume_status_from_pactl(output: str) -> str:
    state = volume_state_from_pactl(output)
    if state is None:
        return ""
    return f"{state.percent}%"


def read_volume_state() -> VolumeState | None:
    state = volume_state_from_wpctl(run_text_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]))
    if state is not None:
        return state

    return volume_state_from_pactl(
        run_text_command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]),
        run_text_command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"]),
    )


def read_volume_status() -> str:
    state = read_volume_state()
    if state is None:
        return "Vol"
    return "Mute" if state.muted else f"{state.percent}%"


def run_system_command(argv: list[str], timeout: float = 2.0) -> bool:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return False
    return completed.returncode == 0


def set_system_volume(percent: int) -> bool:
    percent = max(0, min(percent, 100))
    if run_system_command(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%"]):
        return True
    return run_system_command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])


def set_system_muted(muted: bool) -> bool:
    value = "1" if muted else "0"
    if run_system_command(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", value]):
        return True
    return run_system_command(["pactl", "set-sink-mute", "@DEFAULT_SINK@", value])


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
    parser = argparse.ArgumentParser(description="ai-bar side toolbar")
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
        print(f"ai-bar: configurazione non valida: {exc}", file=sys.stderr)
        return 2

    AiBarWindow(config, args.config)
    Gtk.main()
    return 0
