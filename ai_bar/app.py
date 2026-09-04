from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk, Pango, Vte

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
    gi.require_version("Secret", "1")
    from gi.repository import Secret
except Exception:  # pragma: no cover - exercised only on systems without Secret.
    Secret = None

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
WINDOW_ICON_SIZE = 20
PANEL_ANIMATION_INTERVAL_MS = 16
PANEL_ANIMATION_MIN_STEP = 18
VOLUME_UPDATE_DELAY_MS = 120
LAUNCH_MAXIMIZE_INTERVAL_MS = 100
LAUNCH_MAXIMIZE_ATTEMPTS = 50
EMBED_POLL_INTERVAL_MS = 150
EMBED_POLL_ATTEMPTS = 60
WEBVIEW_ZOOM_STEP = 0.1
FAVICON_SIZE = 24
PAGE_ACTION_ICON_SIZE = 16
FAVICON_TIMEOUT_SECONDS = 5
FAVICON_MAX_BYTES = 1 << 20
ASKPASS_PATH = "/usr/local/bin/ai-bar-askpass"
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


LOGIN_FILL_JS = """
(function () {
  var user = document.querySelector('input[autocomplete="username"]')
          || document.querySelector('input[name="username"]')
          || document.querySelector('input[type="text"]');
  var pass = document.querySelector('input[type="password"]');
  if (!user || !pass) { return; }
  if (user.value || pass.value) { return; }
  user.value = %s;
  pass.value = %s;
  [user, pass].forEach(function (field) {
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));
  });
})();
"""


LOGIN_CAPTURE_JS = """
(function () {
  if (window.__aiBarLoginHook) { return; }
  window.__aiBarLoginHook = true;
  function grab(form) {
    var pass = form.querySelector('input[type="password"]');
    if (!pass || !pass.value) { return; }
    var user = form.querySelector('input[autocomplete="username"]')
            || form.querySelector('input[name="username"]')
            || form.querySelector('input[type="text"]');
    if (!user || !user.value) { return; }
    window.webkit.messageHandlers.aiBarLogin.postMessage(
      JSON.stringify({ username: user.value, password: pass.value }));
  }
  // L'evento submit copre il tasto Invio e il click sul bottone; l'ascolto e'
  // in cattura perche' una pagina che chiama preventDefault fermerebbe la fase
  // di bubbling prima che arrivi qui.
  document.addEventListener('submit', function (event) {
    if (event.target && event.target.tagName === 'FORM') { grab(event.target); }
  }, true);
})();
"""


def script_message_text(result: Any) -> str:
    # WebKit 4.1 consegna un JSCValue, le versioni precedenti un
    # JavascriptResult da cui va estratto: si accettano entrambi.
    value = result
    if hasattr(result, "get_js_value"):
        value = result.get_js_value()
    try:
        return value.to_string()
    except Exception:
        return ""


def clean_window_title(title: str) -> str:
    return " ".join(title.split()) or "Finestra"


def application_id(value: str) -> str:
    name = Path(value).name.lower()
    if name.endswith(".desktop"):
        name = name[:-8]
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def application_ids_match(launcher_id: str, window_id: str) -> bool:
    launcher_id = application_id(launcher_id)
    window_id = application_id(window_id)
    if not launcher_id or not window_id:
        return False
    return (
        launcher_id == window_id
        or window_id.startswith(launcher_id + "-")
        or launcher_id.startswith(window_id + "-")
        or window_id.endswith("-" + launcher_id)
        or launcher_id.endswith("-" + window_id)
    )


def launcher_application_id(button_config: dict[str, Any]) -> str:
    command = button_config.get("command")
    if isinstance(command, str):
        parts = shlex.split(command)
        executable = parts[0] if parts else ""
    else:
        executable = command[0] if command else ""
    return application_id(str(executable))


def terminal_tab_label(command: str | list[str] | None) -> str:
    if not command:
        return "Terminale"
    first = shlex.split(command)[0] if isinstance(command, str) else command[0]
    name = Path(first).name
    return {"ds-code": "DS Code"}.get(name, name.capitalize())


def webkit_data_directory() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return root / "ai-bar" / "webkit"


def webkit_cookie_storage_path() -> Path:
    return webkit_data_directory() / "cookies.sqlite"


def webkit_favicon_database_directory() -> Path:
    return webkit_data_directory() / "favicons"


def favicon_cache_path(url: str) -> Path:
    # WebKit ricorda le favicon che sa decodificare, non quelle che scarichiamo
    # noi: senza questa copia il bottone ripartirebbe con l'icona di riserva a
    # ogni avvio, fino alla prima apertura del sito.
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return webkit_data_directory() / "icons" / f"button-{digest}.png"


def same_origin(first: str, second: str) -> bool:
    # Confronto schema/host/porta: basta a decidere se una pagina e' ancora
    # quella per cui le credenziali erano state salvate.
    try:
        a, b = urllib.parse.urlsplit(first), urllib.parse.urlsplit(second)
    except ValueError:
        return False
    if not a.scheme or not a.netloc or not b.scheme or not b.netloc:
        return False
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)




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


def focus_x11_window(xid: int) -> bool:
    if X is None or xlib_display is None:
        return False

    display = None
    errors: list[Any] = []
    try:
        display = xlib_display.Display()
        window = display.create_resource_object("window", xid)
        window.set_input_focus(
            X.RevertToParent,
            X.CurrentTime,
            onerror=errors.append,
        )
        display.sync()
        return not errors
    except Exception:
        return False
    finally:
        if display is not None:
            display.close()


def centered_position(area: Any, width: int, height: int) -> tuple[int, int]:
    # Top left corner that centres a window of this size inside the area. A
    # window larger than the area rests on the origin rather than taking
    # negative coordinates, which would push it off screen.
    x = area.x + max(0, (area.width - width) // 2)
    y = area.y + max(0, (area.height - height) // 2)
    return x, y


def spiral_rectangles(area: Any, count: int) -> list[tuple[int, int, int, int]]:
    rectangles = []
    x, y, width, height = area.x, area.y, area.width, area.height
    start_direction = 1 if width < height else 0
    for index in range(max(0, count - 1)):
        direction = (start_direction + index) % 4
        if direction == 0:
            split = width // 2
            rectangles.append((x, y, split, height))
            x += split
            width -= split
        elif direction == 1:
            split = height // 2
            rectangles.append((x, y, width, split))
            y += split
            height -= split
        elif direction == 2:
            split = width // 2
            rectangles.append((x + width - split, y, split, height))
            width -= split
        else:
            split = height // 2
            rectangles.append((x, y + height - split, width, split))
            height -= split
    if count > 0:
        rectangles.append((x, y, width, height))
    return rectangles


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
    app_id: str = ""


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

button.window-button.open-window {
  border-color: #63b68e;
}

button.launcher-button.active-launcher {
  background: #314238;
  border-color: #63b68e;
}

button.launcher-button.detached-launcher {
  border-color: #63b68e;
  border-style: dashed;
}

.detach-bar {
  margin-bottom: 2px;
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

button.quick-launcher-button {
  background: transparent;
  border: 0;
  border-radius: 4px;
  padding: 3px;
}

button.quick-launcher-button:hover {
  background: #2d3335;
}

scale.volume-slider {
  min-width: 100px;
}

.volume-percent {
  min-width: 38px;
}

button.launcher-button {
  min-height: 48px;
}

button.launcher-button.icon-only-launcher {
  min-height: 36px;
}

.status-area {
  margin-bottom: 2px;
}

.status-flow {
  margin-bottom: 0;
}

.tray-window-separator {
  background-color: #c0c0c0;
}

.window-flow {
  margin-top: 2px;
}

.tray-icon-cell {
  background: transparent;
  border: 0;
  border-radius: 6px;
  min-height: 24px;
  min-width: 24px;
  padding: 2px;
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
        self.embedded_window_xids: dict[str, int] = {}
        self.launcher_buttons: dict[str, Gtk.Widget] = {}
        self.detached: dict[str, Gtk.Window] = {}
        self.detach_button: Gtk.Widget | None = None
        self.reload_button: Gtk.Widget | None = None
        self.favicon_targets: dict[str, Gtk.Image] = {}
        self.favicon_fetched: set[str] = set()
        self.terminal_notebook: Gtk.Notebook | None = None
        self.wnck_screen: Any = None
        self.window_flow: Gtk.FlowBox | None = None
        self.window_children: list[Gtk.FlowBoxChild] = []
        self.window_list_signature: tuple[tuple[int, str, str, bool], ...] = ()
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

        # Il database delle favicon va abilitato prima di qualunque
        # caricamento, altrimenti le icone non vengono nemmeno registrate.
        favicon_directory = webkit_favicon_database_directory()
        favicon_directory.mkdir(parents=True, exist_ok=True)
        self.web_context.set_favicon_database_directory(str(favicon_directory))
        self.web_context.get_favicon_database().connect(
            "favicon-changed", self._on_favicon_changed)

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_border_width(10)

        content.pack_start(self._build_clock(), False, False, 0)
        content.pack_start(self._build_tray_row(), False, False, 0)

        for group in self.config.get("launcher_groups", []):
            internal_buttons = [
                button for button in group.get("buttons", [])
                if button.get("target") is not None
            ]
            if internal_buttons:
                content.pack_start(
                    self._build_launcher_group({**group, "buttons": internal_buttons}),
                    False,
                    False,
                    0,
                )

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
        content.pack_start(self._build_detach_bar(), False, False, 0)
        content.pack_start(self.terminal_notebook, True, True, 0)
        content.pack_start(self._build_session_buttons(), False, False, 0)

        resizable = bool(self.config["panel"].get("resizable", True))
        if resizable and self.config["panel"].get("side", "left") == "right":
            root.pack_start(self._build_resize_handle(), False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.NONE)
        scroller.add(content)
        root.pack_start(scroller, True, True, 0)

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

        tray_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tray_row.pack_start(tray_flow, True, True, 0)
        tray_row.pack_end(self._build_spiral_tile_button(), False, False, 0)
        tray_row.pack_end(self._build_macro_recorder_button(), False, False, 0)

        self.window_flow = Gtk.FlowBox()
        self.window_flow.get_style_context().add_class("window-flow")
        self.window_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.window_flow.set_min_children_per_line(1)
        self.window_flow.set_max_children_per_line(20)
        self.window_flow.set_column_spacing(6)
        self.window_flow.set_row_spacing(6)
        self.window_children = []
        self._rebuild_window_buttons([])

        refresh = int(self.config.get("tray", {}).get("status_refresh_seconds", 5))
        if self.status_labels or self.volume_controls:
            self._update_status_items()
            GLib.timeout_add_seconds(max(1, refresh), self._update_status_items)

        status_area.pack_start(status_flow, False, False, 0)
        status_area.pack_start(tray_row, False, False, 0)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.get_style_context().add_class("tray-window-separator")
        status_area.pack_start(separator, False, False, 0)
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
        if item.get("type") == "screenshot" and command == [
            "/usr/bin/mate-screenshot",
            "/home/user/Immagini/screenshot_%Y-%m-%d_%H-%M-%S.png",
        ]:
            command = ["/usr/bin/mate-screenshot", "--interactive"]
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

    def _build_spiral_tile_button(self) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("tray-icon-cell")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text("Affianca le finestre a chiocciola")
        image = Gtk.Image.new_from_icon_name("view-grid-symbolic", Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        button.add(image)
        button.connect("clicked", self._tile_windows_spiral)
        return button

    def _build_macro_recorder_button(self) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("tray-icon-cell")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text("Avvia Macro Recorder")
        image = Gtk.Image.new_from_icon_name("media-record-symbolic", Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        button.add(image)
        button.connect("clicked", lambda _button: self._launch(["macro-recorder"]))
        return button

    def _open_configuration_assistant(self) -> None:
        config_path = self.config_path or default_config_path()
        command = configuration_assistant_command(config_path)
        key = terminal_session_key(command)
        detached_window = self.detached.pop(key, None)
        if detached_window is not None:
            detached_page = detached_window.get_child()
            if detached_page is not None:
                detached_window.remove(detached_page)
            detached_window.destroy()
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

        if info.icon is not None:
            icon = info.icon.scale_simple(
                WINDOW_ICON_SIZE,
                WINDOW_ICON_SIZE,
                GdkPixbuf.InterpType.BILINEAR,
            )
            image = Gtk.Image.new_from_pixbuf(icon)
        else:
            image = Gtk.Image.new_from_icon_name("window-symbolic", Gtk.IconSize.MENU)
            image.set_pixel_size(WINDOW_ICON_SIZE)
        button.add(image)
        return button

    def _build_pinned_launcher_button(
        self, button_config: dict[str, Any], windows: list[WindowInfo]
    ) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("window-button")
        if windows:
            button.get_style_context().add_class("open-window")
        if any(window.active for window in windows):
            button.get_style_context().add_class("active-window")
        button.set_tooltip_text(str(button_config.get("label", "")))
        button.set_relief(Gtk.ReliefStyle.NONE)

        if windows:
            target = next(
                (window for window in reversed(windows) if window.active),
                windows[-1],
            )
            button.connect(
                "clicked", lambda _button: self._activate_window(target.xid)
            )
        else:
            button.connect(
                "clicked",
                lambda _button: self._launch(
                    button_config["command"],
                    maximized=bool(button_config.get("maximized", False)),
                ),
            )

        icon_name = button_config.get("icon")
        if icon_name:
            image = Gtk.Image.new_from_icon_name(str(icon_name), Gtk.IconSize.MENU)
            image.set_pixel_size(WINDOW_ICON_SIZE)
        else:
            window_icon = windows[-1].icon if windows else None
            if window_icon is not None:
                icon = window_icon.scale_simple(
                    WINDOW_ICON_SIZE,
                    WINDOW_ICON_SIZE,
                    GdkPixbuf.InterpType.BILINEAR,
                )
                image = Gtk.Image.new_from_pixbuf(icon)
            else:
                image = Gtk.Image.new_from_icon_name(
                    "application-x-executable-symbolic", Gtk.IconSize.MENU
                )
                image.set_pixel_size(WINDOW_ICON_SIZE)
        button.add(image)
        return button

    def _rebuild_window_buttons(self, windows: list[WindowInfo]) -> None:
        for child in self.window_children:
            child.destroy()
        self.window_children.clear()

        remaining = list(windows)
        for group in self.config.get("launcher_groups", []):
            for button_config in group.get("buttons", []):
                if button_config.get("target") is not None:
                    continue
                launcher_id = launcher_application_id(button_config)
                matches = [
                    window for window in remaining
                    if application_ids_match(launcher_id, window.app_id)
                ]
                matched_xids = {window.xid for window in matches}
                remaining = [
                    window for window in remaining if window.xid not in matched_xids
                ]
                child = self._add_flow_child(
                    self.window_flow,
                    self._build_pinned_launcher_button(button_config, matches),
                )
                self.window_children.append(child)

        window_groups: dict[str, list[WindowInfo]] = {}
        for window in remaining:
            key = window.app_id or f"window:{window.xid}"
            window_groups.setdefault(key, []).append(window)
        for group in window_groups.values():
            representative = next(
                (window for window in reversed(group) if window.active),
                group[-1],
            )
            child = self._add_flow_child(
                self.window_flow, self._build_window_button(representative)
            )
            self.window_children.append(child)

        self.window_flow.show_all()

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
            button = self._build_launcher_button(button_config, show_label=bool(title))
            self._add_flow_child(flow, button)

        box.pack_start(flow, False, False, 0)
        return box

    def _build_launcher_button(
        self, button_config: dict[str, Any], show_label: bool = True
    ) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("launcher-button")
        if not show_label:
            button.get_style_context().add_class("icon-only-launcher")
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

        page_key = launcher_page_key(button_config)
        if page_key is not None:
            self.launcher_buttons[page_key] = button

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_hexpand(True)

        icon_name = button_config.get("icon")
        if icon_name:
            image = Gtk.Image.new_from_icon_name(str(icon_name), Gtk.IconSize.DIALOG)
            image.set_pixel_size(24)
            inner.pack_start(image, False, False, 0)
            if target == "url":
                self._apply_favicon(image, str(button_config.get("url", "")))

        if show_label:
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
        if isinstance(gdk_window, GdkX11.X11Window):
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
        if os.environ.get("AI_BAR_READY_FILE"):
            GLib.timeout_add(50, self._mark_session_ready)

    def _mark_session_ready(self) -> bool:
        if (
            self.xapp_tray_host is not None
            and self.xapp_tray_host.monitor is not None
            and not self.xapp_tray_host.is_registered()
        ):
            return True

        try:
            Path(os.environ["AI_BAR_READY_FILE"]).touch()
        except OSError as exc:
            print(f"ai-bar: impossibile segnalare la tray pronta: {exc}", file=sys.stderr)
        return False

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
        page: Gtk.Widget,
        _page: int,
    ) -> None:
        if isinstance(page, Vte.Terminal):
            self.terminal = page
        self._highlight_launcher(page)
        # La pagina arriva come argomento perche' durante switch-page il
        # notebook non ha ancora aggiornato la propria pagina corrente:
        # chiederglielo qui darebbe ancora quella di prima.
        self._refresh_launcher_states(page)

    def _highlight_launcher(self, page: Gtk.Widget) -> None:
        # Il bottone dello strumento mostrato resta in evidenza: le schede sono
        # nascoste, quindi senza questo nulla dice quale strumento si sta
        # guardando. La scheda iniziale non ha un bottone, e in quel caso
        # nessuno resta acceso.
        shown = self._page_key(page)
        for key, button in self.launcher_buttons.items():
            style = button.get_style_context()
            if key == shown:
                style.add_class("active-launcher")
            else:
                style.remove_class("active-launcher")


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
                class_group = window.get_class_group()
                class_id = class_group.get_id() if class_group is not None else None
                if not class_id:
                    class_id = window.get_class_group_name()
                windows.append(
                    WindowInfo(
                        xid=xid,
                        title=title,
                        active=window == active_window,
                        icon=window.get_mini_icon(),
                        app_id=application_id(class_id or ""),
                    )
                )
        except Exception as exc:
            print(f"ai-bar: elenco finestre non aggiornato: {exc}", file=sys.stderr)
            return True

        signature = tuple(
            (window.xid, window.title, window.app_id, window.active)
            for window in windows
        )
        if signature == self.window_list_signature:
            return True

        self._rebuild_window_buttons(windows)
        self.window_list_signature = signature
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

    def _focused_monitor(self, window: Any | None) -> Any | None:
        if window is None:
            return None
        try:
            if self.own_xid is not None and int(window.get_xid()) == self.own_xid:
                return None
            x, y, width, height = window.get_geometry()
            display = Gdk.Display.get_default()
            if display is None:
                return None
            return display.get_monitor_at_point(
                x + width // 2,
                y + height // 2,
            )
        except Exception:
            return None

    def _tile_windows_spiral(self, _button: Gtk.Button | None = None) -> None:
        if Wnck is None or self.wnck_screen is None:
            return

        try:
            self.wnck_screen.force_update()
            active_workspace = self.wnck_screen.get_active_workspace()
            active_window = self.wnck_screen.get_active_window()
            monitor = self._monitor_geometry()
            workarea = self._monitor_workarea()
            focused_monitor = self._focused_monitor(active_window)
            if focused_monitor is not None:
                monitor = focused_monitor.get_geometry()
                try:
                    workarea = focused_monitor.get_workarea()
                except Exception:
                    workarea = monitor
            windows = []
            for window in reversed(self.wnck_screen.get_windows_stacked()):
                xid = int(window.get_xid())
                if (
                    xid == self.own_xid
                    or window.is_skip_tasklist()
                    or window.is_minimized()
                    or window.get_window_type() != Wnck.WindowType.NORMAL
                ):
                    continue
                if (
                    active_workspace is not None
                    and not window.is_pinned()
                    and not window.is_on_workspace(active_workspace)
                ):
                    continue
                x, y, width, height = window.get_geometry()
                center_x = x + width // 2
                center_y = y + height // 2
                if not (
                    monitor.x <= center_x < monitor.x + monitor.width
                    and monitor.y <= center_y < monitor.y + monitor.height
                ):
                    continue
                windows.append(window)

            mask = (
                Wnck.WindowMoveResizeMask.X
                | Wnck.WindowMoveResizeMask.Y
                | Wnck.WindowMoveResizeMask.WIDTH
                | Wnck.WindowMoveResizeMask.HEIGHT
            )
            for window, rectangle in zip(
                windows, spiral_rectangles(workarea, len(windows))
            ):
                window.unmaximize()
                window.set_geometry(
                    Wnck.WindowGravity.CURRENT,
                    mask,
                    *rectangle,
                )
        except Exception as exc:
            print(f"ai-bar: finestre non affiancate: {exc}", file=sys.stderr)

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
        # Se e' stato staccato, il suo posto non e' piu' nel notebook: senza
        # questo, page_num tornerebbe -1 e set_current_page(-1) selezionerebbe
        # l'ultima scheda invece di dirlo.
        detached = self.detached.get(key)
        if detached is not None:
            detached.present()
            return

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

        key = "window:" + command_to_shell_line(command)
        detached = self.detached.get(key)
        if detached is not None:
            detached.present()
            widget = self.embedded.get(key)
            if widget is not None and key in self.embedded_window_xids:
                GLib.idle_add(self._focus_embedded_window, widget)
            return

        if Wnck is None or self.wnck_screen is None:
            print("ai-bar: libwnck non disponibile, finestra aperta esternamente.", file=sys.stderr)
            self._launch(command)
            return

        widget = self.embedded.get(key)
        if widget is None:
            try:
                self.wnck_screen.force_update()
                existing_xids = {
                    int(window.get_xid())
                    for window in self.wnck_screen.get_windows_stacked()
                }
            except Exception as exc:
                print(f"ai-bar: stato finestre non disponibile: {exc}", file=sys.stderr)
                self._launch(command)
                return
            try:
                if isinstance(command, str):
                    subprocess.Popen(command, shell=True, start_new_session=True)
                else:
                    subprocess.Popen(command, start_new_session=True)
            except Exception as exc:
                self._show_error(f"Comando non avviato: {command}\n{exc}")
                return
            socket = self._build_embedded_socket(key)
            socket.connect("realize", self._embed_launched_window, existing_xids)
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
        widget.grab_focus()
        if key in self.embedded_window_xids:
            GLib.idle_add(self._focus_embedded_window, widget)

    def _build_embedded_socket(self, key: str) -> Gtk.Socket:
        socket = Gtk.Socket()
        socket.set_hexpand(True)
        socket.set_vexpand(True)
        socket.connect("destroy", self._on_embedded_window_destroyed, key)
        return socket

    def _on_embedded_window_destroyed(
        self, socket: Gtk.Socket, key: str
    ) -> None:
        if self.embedded.get(key) is socket:
            self.embedded.pop(key, None)
            self.embedded_window_xids.pop(key, None)

    def _focus_embedded_window(self, socket: Gtk.Socket) -> bool:
        key = self._page_key(socket)
        if key is not None:
            xid = self.embedded_window_xids.get(key)
            if xid is not None:
                focus_x11_window(xid)
        return False

    def _embed_launched_window(
        self, socket: Gtk.Socket, existing_xids: set[int]
    ) -> None:
        if self.wnck_screen is None:
            return
        key = self._page_key(socket)
        if key is None:
            return
        attempts_remaining = EMBED_POLL_ATTEMPTS

        def embed_when_ready() -> bool:
            nonlocal attempts_remaining
            try:
                self.wnck_screen.force_update()
                for window in self.wnck_screen.get_windows_stacked():
                    xid = int(window.get_xid())
                    if (
                        xid != self.own_xid
                        and xid not in existing_xids
                        and xid not in self.embedded_window_xids.values()
                        and not window.is_skip_tasklist()
                    ):
                        self.embedded_window_xids[key] = xid
                        try:
                            socket.add_id(xid)
                        except Exception:
                            self.embedded_window_xids.pop(key, None)
                            raise
                        detach_button = getattr(self, "detach_button", None)
                        if detach_button is not None:
                            detach_button.set_sensitive(True)
                        GLib.idle_add(self._focus_embedded_window, socket)
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
        # Se e' stato staccato, il suo posto non e' piu' nel notebook: senza
        # questo, page_num tornerebbe -1 e set_current_page(-1) selezionerebbe
        # l'ultima scheda invece di dirlo.
        detached = self.detached.get(key)
        if detached is not None:
            detached.present()
            return

        widget = self.embedded.get(key)
        if widget is None:
            manager = WebKit2.UserContentManager()
            manager.register_script_message_handler("aiBarLogin")
            manager.connect(
                "script-message-received::aiBarLogin",
                lambda _manager, result: self._on_login_submitted(
                    url, script_message_text(result)),
            )
            manager.add_script(WebKit2.UserScript.new(
                LOGIN_CAPTURE_JS,
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.END,
                None, None,
            ))
            webview = WebKit2.WebView(
                web_context=self.web_context or WebKit2.WebContext.get_default(),
                user_content_manager=manager,
            )
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
            # Il segnale del database scatta solo la prima volta che il sito
            # viene visto: l'apertura della scheda e' l'altra occasione buona
            # per andare a cercare l'icona.
            webview.connect("load-changed", self._on_web_load_changed, url)
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

    def _build_detach_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.get_style_context().add_class("detach-bar")
        reload_button = Gtk.Button()
        reload_button.set_relief(Gtk.ReliefStyle.NONE)
        reload_button.set_tooltip_text("Ricarica il tool corrente")
        reload_icon = Gtk.Image.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.MENU)
        reload_icon.set_pixel_size(PAGE_ACTION_ICON_SIZE)
        reload_button.add(reload_icon)
        reload_button.connect("clicked", lambda _button: self._reload_current_page())
        reload_button.set_sensitive(False)

        detach_button = Gtk.Button()
        detach_button.get_style_context().add_class("detach-button")
        detach_button.set_relief(Gtk.ReliefStyle.NONE)
        detach_button.set_tooltip_text("Stacca in una finestra sul monitor principale")
        detach_icon = Gtk.Image.new_from_icon_name(
            "window-new-symbolic", Gtk.IconSize.MENU)
        detach_icon.set_pixel_size(PAGE_ACTION_ICON_SIZE)
        detach_button.add(detach_icon)
        detach_button.connect("clicked", lambda _button: self._detach_current_page())
        detach_button.set_sensitive(False)

        bar.pack_end(detach_button, False, False, 0)
        bar.pack_end(reload_button, False, False, 0)
        for button_config in reversed(self.config.get("quick_launchers", [])):
            bar.pack_end(
                self._build_quick_launcher_button(button_config),
                False,
                False,
                0,
            )
        self.detach_button = detach_button
        self.reload_button = reload_button
        return bar

    def _build_quick_launcher_button(self, button_config: dict[str, Any]) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("quick-launcher-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(str(button_config.get("label", "")))
        icon = Gtk.Image.new_from_icon_name(
            str(button_config.get("icon", "")), Gtk.IconSize.MENU)
        icon.set_pixel_size(PAGE_ACTION_ICON_SIZE)
        button.add(icon)
        command = button_config["command"]
        if button_config.get("integrated", False):
            label = str(button_config.get("label", ""))
            button.connect(
                "clicked",
                lambda _button: self._switch_embedded_window(command, label),
            )
        else:
            button.connect("clicked", lambda _button: self._launch(command))
        return button

    def _detachable_page(self, page: Gtk.Widget | None) -> bool:
        if page is None:
            return False
        if not isinstance(page, Gtk.Socket):
            return True
        key = self._page_key(page)
        return key is not None and key in self.embedded_window_xids

    def _page_key(self, page: Gtk.Widget) -> str | None:
        for pages in (self.terminals, self.embedded):
            for key, widget in pages.items():
                if widget is page:
                    return key
        return None

    def _detach_current_page(self) -> None:
        notebook = self.terminal_notebook
        if notebook is None:
            return
        index = notebook.get_current_page()
        page = notebook.get_nth_page(index) if index >= 0 else None
        if not self._detachable_page(page):
            return
        key = self._page_key(page)
        if key is None or key in self.detached:
            return

        title = notebook.get_tab_label_text(page) or "ai-bar"
        window = Gtk.Window(title=f"{title} \u2014 ai-bar")
        window.set_default_size(900, 700)
        window.connect("delete-event", self._on_detached_closed, key)

        header = Gtk.HeaderBar(title=title)
        header.set_show_close_button(True)
        reload_button = Gtk.Button()
        reload_button.set_tooltip_text("Ricarica il tool corrente")
        reload_icon = Gtk.Image.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.MENU)
        reload_icon.set_pixel_size(PAGE_ACTION_ICON_SIZE)
        reload_button.add(reload_icon)
        reload_button.connect(
            "clicked", lambda _button: self._reload_page(window.get_child()))
        reattach_button = Gtk.Button()
        reattach_button.set_tooltip_text("Riattacca al pannello")
        reattach_icon = Gtk.Image.new_from_icon_name(
            "window-new-symbolic", Gtk.IconSize.MENU)
        reattach_icon.set_pixel_size(PAGE_ACTION_ICON_SIZE)
        reattach_button.add(reattach_icon)
        reattach_button.connect(
            "clicked", lambda _button: self._reattach(key, window))
        header.pack_end(reattach_button)
        header.pack_end(reload_button)
        window.set_titlebar(header)

        if isinstance(page, Gtk.Socket):
            xid = self.embedded_window_xids.get(key)
            if xid is None:
                window.destroy()
                return
            detached_page = self._build_embedded_socket(key)
            window.add(detached_page)
            window.show_all()
            self.embedded[key] = detached_page
            try:
                detached_page.add_id(xid)
            except Exception as exc:
                self.embedded[key] = page
                window.destroy()
                self._show_error(f"Finestra non staccata: {exc}")
                return
            notebook.remove(page)
            page.destroy()
            GLib.idle_add(self._focus_embedded_window, detached_page)
        else:
            notebook.remove(page)
            window.add(page)
            window.show_all()

        self.detached[key] = window
        self._place_detached(window)

        # Si torna alla prima scheda rimasta. Se non ne resta nessuna l'area
        # resta vuota, che e' la verita': quel contenuto ora sta in una
        # finestra. Fabbricare una scheda di rimpiazzo la renderebbe
        # irraggiungibile, perche' le linguette sono nascoste e a una scheda
        # ci si arriva solo dal pulsante che la possiede.
        if notebook.get_n_pages() > 0:
            notebook.set_current_page(0)
        self._refresh_launcher_states()

    def _place_detached(self, window: Gtk.Window) -> None:
        # Stessa scelta dei programmi lanciati dai bottoni: il monitor del
        # pannello e' quello che si voleva liberare.
        area = self._launch_area()
        if area is None:
            return
        width, height = window.get_size()
        x, y = centered_position(area, width, height)
        window.move(x, y)

    def _on_detached_closed(self, window: Gtk.Window, _event: Any, key: str) -> bool:
        # La vista rientra nel pannello invece di morire: chiudere una finestra
        # non deve costare una sessione di lavoro. Si ritorna True perche' la
        # finestra la distruggiamo noi, dopo aver messo al sicuro il contenuto.
        self._reattach(key, window)
        return True

    def _reattach(self, key: str, window: Gtk.Window) -> None:
        notebook = self.terminal_notebook
        page = window.get_child()
        title = (window.get_title() or "").removesuffix(" \u2014 ai-bar")
        if notebook is None or page is None:
            self.detached.pop(key, None)
            if page is not None:
                window.remove(page)
            window.destroy()
            return

        if isinstance(page, Gtk.Socket):
            xid = self.embedded_window_xids.get(key)
            if xid is None:
                return
            panel_page = self._build_embedded_socket(key)
            index = notebook.append_page(
                panel_page,
                Gtk.Label(label=title or "Scheda"),
            )
            panel_page.show_all()
            self.embedded[key] = panel_page
            try:
                panel_page.add_id(xid)
            except Exception as exc:
                self.embedded[key] = page
                notebook.remove(panel_page)
                panel_page.destroy()
                self._show_error(f"Finestra non riattaccata: {exc}")
                return
            window.remove(page)
            page.destroy()
            GLib.idle_add(self._focus_embedded_window, panel_page)
        else:
            window.remove(page)
            index = notebook.append_page(page, Gtk.Label(label=title or "Scheda"))
            page.show_all()

        self.detached.pop(key, None)
        window.destroy()
        notebook.set_current_page(index)
        self.present()
        self._refresh_launcher_states()

    def _reloadable_page(self, page: Gtk.Widget | None) -> bool:
        return page is not None and self._page_key(page) is not None

    def _reload_current_page(self) -> None:
        notebook = self.terminal_notebook
        if notebook is None:
            return
        index = notebook.get_current_page()
        page = notebook.get_nth_page(index) if index >= 0 else None
        self._reload_page(page)

    def _reload_page(self, page: Gtk.Widget | None) -> None:
        if not self._reloadable_page(page):
            return
        key = self._page_key(page)
        if key is None:
            return

        if key in self.embedded:
            if isinstance(page, Gtk.Socket):
                notebook = self.terminal_notebook
                if notebook is None:
                    return
                title = notebook.get_tab_label_text(page) or "Scheda"
                command = key.removeprefix("window:")
                self.embedded.pop(key, None)
                page.destroy()
                self._switch_embedded_window(command, title)
                return
            page.reload()
            return

        notebook = self.terminal_notebook
        detached_window = self.detached.get(key)
        if detached_window is None and notebook is None:
            return

        command = None if key == "__shell__" else key
        replacement = self._build_terminal(command, width_px=self.panel_width)
        self.terminals[key] = replacement
        replacement.show_all()

        if detached_window is not None:
            detached_window.remove(page)
            detached_window.add(replacement)
            detached_window.present()
        elif notebook is not None:
            index = notebook.page_num(page)
            title = notebook.get_tab_label_text(page) or "Scheda"
            notebook.remove(page)
            notebook.insert_page(replacement, Gtk.Label(label=title), index)
            notebook.set_current_page(index)

        page.destroy()
        self.terminal = replacement
        replacement.grab_focus()
        self._refresh_launcher_states(replacement)

    def _refresh_launcher_states(self, page: Gtk.Widget | None = None) -> None:
        notebook = self.terminal_notebook
        if page is None and notebook is not None:
            index = notebook.get_current_page()
            page = notebook.get_nth_page(index) if index >= 0 else None
        if self.detach_button is not None:
            self.detach_button.set_sensitive(self._detachable_page(page))
        if self.reload_button is not None:
            self.reload_button.set_sensitive(self._reloadable_page(page))
        for key, button in self.launcher_buttons.items():
            style = button.get_style_context()
            if key in self.detached:
                style.add_class("detached-launcher")
            else:
                style.remove_class("detached-launcher")

    def _on_web_load_changed(self, view: Any, event: Any, url: str) -> None:
        if event != WebKit2.LoadEvent.FINISHED:
            return
        self._fill_login(view, url)
        image = self.favicon_targets.get(url)
        if image is not None:
            self._apply_favicon(image, url, view.get_uri())

    def _show_favicon(self, image: Gtk.Image, pixbuf: Any, url: str) -> None:
        scaled = pixbuf.scale_simple(
            FAVICON_SIZE, FAVICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
        if scaled is None:
            return
        image.set_from_pixbuf(scaled)
        cached = favicon_cache_path(url)
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            scaled.savev(str(cached), "png", [], [])
        except Exception as exc:
            print(f"ai-bar: favicon non salvata: {exc}", file=sys.stderr)

    def _apply_favicon(self, image: Gtk.Image, url: str,
                       page_uri: str | None = None,
                       icon_uri: str | None = None) -> None:
        if WebKit2 is None or not url:
            return
        self.favicon_targets[url] = image

        cached = favicon_cache_path(url)
        if cached.exists():
            try:
                image.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file(str(cached)))
            except Exception:
                pass

        database = (self.web_context
                    or WebKit2.WebContext.get_default()).get_favicon_database()

        def done(db: Any, result: Any, _data: Any) -> None:
            surface = None
            try:
                surface = db.get_favicon_finish(result)
            except Exception:
                # Sito mai aperto, senza favicon, oppure in un formato che
                # WebKit non sa decodificare: gli SVG finiscono tutti qui.
                surface = None
            pixbuf = None
            if surface is not None:
                pixbuf = Gdk.pixbuf_get_from_surface(
                    surface, 0, 0, surface.get_width(), surface.get_height())
            if pixbuf is None:
                target = icon_uri
                if not target:
                    try:
                        target = db.get_favicon_uri(page_uri or url)
                    except Exception:
                        target = None
                self._download_favicon(image, url, target)
                return
            self._show_favicon(image, pixbuf, url)

        database.get_favicon(page_uri or url, None, done, None)

    def _download_favicon(self, image: Gtk.Image, url: str,
                          icon_uri: str | None) -> None:
        # Anche quando non la decodifica, WebKit ci dice dov'e' l'icona.
        # GdkPixbuf, che si appoggia a librsvg, di solito ce la fa.
        if not icon_uri or icon_uri in self.favicon_fetched:
            return
        # Si scarica solo dal sito configurato, e solo via http(s).
        if not same_origin(icon_uri, url):
            return
        self.favicon_fetched.add(icon_uri)

        def fetch() -> None:
            try:
                with urllib.request.urlopen(
                        icon_uri, timeout=FAVICON_TIMEOUT_SECONDS) as response:
                    data = response.read(FAVICON_MAX_BYTES)
                loader = GdkPixbuf.PixbufLoader()
                loader.set_size(FAVICON_SIZE, FAVICON_SIZE)
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
            except Exception as exc:
                print(f"ai-bar: favicon non scaricata: {exc}", file=sys.stderr)
                return
            if pixbuf is not None:
                GLib.idle_add(self._show_favicon, image, pixbuf, url)

        threading.Thread(target=fetch, daemon=True).start()

    def _on_favicon_changed(self, _database: Any, page_uri: str,
                            icon_uri: str) -> None:
        # L'icona arriva per la pagina effettiva: al primo giro l'indirizzo
        # configurato non basta, perche' il login sta su un percorso diverso.
        # Si confronta quindi l'origine.
        for url, image in list(self.favicon_targets.items()):
            if same_origin(page_uri, url):
                self._apply_favicon(image, url, page_uri, icon_uri)

    def _secret_schema(self) -> Any | None:
        if Secret is None:
            return None
        return Secret.Schema.new(
            "net.aibar.webview",
            Secret.SchemaFlags.NONE,
            {"application": Secret.SchemaAttributeType.STRING,
             "url": Secret.SchemaAttributeType.STRING,
             "username": Secret.SchemaAttributeType.STRING},
        )

    def _web_credentials(self, url: str) -> tuple[str, str] | None:
        # Le credenziali stanno nel portachiavi di sistema, mai nel file di
        # configurazione. La voce stessa fa da interruttore: se non c'e', la
        # compilazione automatica semplicemente non avviene.
        schema = self._secret_schema()
        if schema is None:
            return None
        try:
            items = Secret.password_search_sync(
                schema,
                {"application": "ai-bar", "url": url},
                Secret.SearchFlags.UNLOCK | Secret.SearchFlags.LOAD_SECRETS,
                None,
            )
            for item in items:
                username = item.get_attributes().get("username")
                password = item.retrieve_secret_sync(None)
                if username and password is not None:
                    return username, password.get_text()
        except Exception as exc:
            print(f"ai-bar: credenziali non leggibili dal portachiavi: {exc}",
                  file=sys.stderr)
        return None

    def _fill_login(self, view: Any, url: str) -> None:
        current = view.get_uri() or ""
        # Le credenziali vanno consegnate solo all'origine per cui sono state
        # salvate: dopo un redirect fuori sito riempire il form significherebbe
        # passare la password a qualcun altro.
        if not same_origin(current, url):
            return
        found = self._web_credentials(url)
        if found is None:
            return
        username, password = found
        script = LOGIN_FILL_JS % (json.dumps(username), json.dumps(password))
        try:
            view.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception as exc:
            print(f"ai-bar: login non compilato: {exc}", file=sys.stderr)

    def _store_credentials(self, url: str, username: str, password: str) -> None:
        schema = self._secret_schema()
        if schema is None:
            return
        try:
            Secret.password_store_sync(
                schema,
                {"application": "ai-bar", "url": url, "username": username},
                Secret.COLLECTION_DEFAULT,
                f"ai-bar {urllib.parse.urlsplit(url).hostname or url}",
                password,
                None,
            )
        except Exception as exc:
            print(f"ai-bar: credenziali non salvate: {exc}", file=sys.stderr)

    def _on_login_submitted(self, url: str, payload: str) -> None:
        try:
            data = json.loads(payload)
            username = str(data["username"])
            password = str(data["password"])
        except Exception:
            return
        if not username or not password:
            return
        # Niente domanda se quelle credenziali sono gia' quelle salvate: la
        # richiesta comparirebbe a ogni accesso.
        if self._web_credentials(url) == (username, password):
            return

        host = urllib.parse.urlsplit(url).hostname or url
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Salvare le credenziali di {host} nel portachiavi?",
        )
        dialog.format_secondary_text(
            f"Utente: {username}\nLa password finisce nel portachiavi di "
            "sistema, non nel file di configurazione."
        )
        answer = dialog.run()
        dialog.destroy()
        if answer == Gtk.ResponseType.YES:
            self._store_credentials(url, username, password)

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


def configure_askpass_environment() -> None:
    if "SUDO_ASKPASS" in os.environ:
        return
    if os.access(ASKPASS_PATH, os.X_OK):
        os.environ["SUDO_ASKPASS"] = ASKPASS_PATH


def launcher_page_key(button_config: dict[str, Any]) -> str | None:
    # La stessa chiave con cui la scheda viene registrata: e' cosi' che si
    # risale dal contenuto mostrato al bottone che lo ha aperto.
    target = button_config.get("target")
    if target == "terminal":
        return terminal_session_key(button_config.get("command"))
    if target == "window":
        return "window:" + command_to_shell_line(button_config["command"])
    if target == "url":
        return "url:" + str(button_config.get("url", ""))
    return None


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

    configure_askpass_environment()

    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError, ConfigError) as exc:
        print(f"ai-bar: configurazione non valida: {exc}", file=sys.stderr)
        return 2

    AiBarWindow(config, args.config)
    Gtk.main()
    return 0
