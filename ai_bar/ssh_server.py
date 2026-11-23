"""SSH service control from a tray icon."""

import subprocess
import sys
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


def read_ssh_state() -> tuple[bool, tuple[str, ...]]:
    result = subprocess.run(
        ["systemctl", "show", "--property=Id,LoadState,ActiveState", "ssh.service", "ssh.socket"],
        capture_output=True, text=True, timeout=5,
    )
    if not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or "Stato del servizio non disponibile")
    units = []
    active = False
    for block in result.stdout.strip().split("\n\n"):
        properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if properties.get("LoadState") != "loaded":
            continue
        unit = properties.get("Id")
        if unit not in ("ssh.service", "ssh.socket"):
            continue
        units.append(unit)
        active |= properties.get("ActiveState") in ("active", "activating", "reloading")
    return active, tuple(units)


def set_ssh_active(active: bool, units: tuple[str, ...]) -> None:
    command = ["pkexec", "systemctl", "start", "ssh.service"] if active else [
        "pkexec", "systemctl", "stop", *units,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(
            (result.stderr or result.stdout).strip()
            or f"Comando non riuscito ({result.returncode})"
        )


class SSHServerTray:
    def __init__(self, show_error: Callable[[str], None], icon_size: int) -> None:
        self._show_error = show_error
        self._closed = False
        self._busy = False
        self._active = False
        self._units: tuple[str, ...] = ()
        icon_info = Gtk.IconTheme.get_default().choose_icon(
            ["network-server-symbolic", "image-missing-symbolic"],
            icon_size, Gtk.IconLookupFlags.FORCE_SIZE,
        )
        self.icons = {}
        for name, value in (("green", "#55c878"), ("gray", "#9ea7ac")):
            color = Gdk.RGBA()
            color.parse(value)
            self.icons[name] = icon_info.load_symbolic(color, None, None, None)[0]

        self.menu = Gtk.Menu()
        self.action = Gtk.MenuItem(label="Verifica dello stato…")
        self.action.connect("activate", self._toggle)
        self.menu.append(self.action)
        self.menu.show_all()
        self.icon = Gtk.StatusIcon(
            pixbuf=self.icons["gray"],
            title="Server SSH",
            tooltip_text="Server SSH: verifica dello stato…",
        )
        self.icon.connect("notify::embedded", self._fit_icon)
        self._fit_icon()
        self.icon.connect("activate", lambda icon: self._popup(icon, 0, Gtk.get_current_event_time()))
        self.icon.connect("popup-menu", self._popup)
        self._poll_id = GLib.timeout_add_seconds(5, self._refresh)
        self._refresh()

    def _popup(self, icon: Gtk.StatusIcon, button: int, timestamp: int) -> None:
        self.menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, timestamp)

    def _fit_icon(self, *_args) -> None:
        # GtkStatusIcon padding clips the image in small XEmbed slots.
        for window in Gtk.Window.list_toplevels():
            if window.__gtype__.name == "GtkTrayIcon":
                for image in window.get_children():
                    image.set_margin_start(0)
                    image.set_margin_end(0)
                    image.set_margin_top(0)
                    image.set_margin_bottom(0)

    def _refresh(self) -> bool:
        if self._closed:
            return False
        if not self._busy:
            self._start_work(None)
        return True

    def _toggle(self, _item: Gtk.MenuItem) -> None:
        if self._closed or self._busy or not self._units:
            return
        self.icon.props.tooltip_text = "Server SSH: operazione in corso…"
        self._start_work(not self._active)

    def _start_work(self, active: bool | None) -> None:
        self._busy = True
        self.action.set_sensitive(False)
        threading.Thread(target=self._work, args=(active,), daemon=True).start()

    def _work(self, active: bool | None) -> None:
        error = None
        state = None
        if active is not None:
            try:
                set_ssh_active(active, self._units)
            except Exception as exc:
                error = str(exc)
        try:
            state = read_ssh_state()
        except Exception as exc:
            if active is not None and error is None:
                error = str(exc)
        GLib.idle_add(self._finish, state, error)

    def _finish(self, state: tuple[bool, tuple[str, ...]] | None, error: str | None) -> bool:
        if self._closed:
            return False
        self._busy = False
        self._active, self._units = state or (False, ())
        if state is None:
            color, status, action = "gray", "stato non disponibile", "Stato non disponibile"
        elif not self._units:
            color, status, action = "gray", "installa openssh-server", "Non installato"
        elif self._active:
            color, status, action = "green", "attivo", "Disattiva"
        else:
            color, status, action = "gray", "disattivato", "Attiva"
        self.icon.props.pixbuf = self.icons[color]
        self.icon.props.tooltip_text = f"Server SSH: {status}"
        self.action.set_label(action)
        self.action.set_sensitive(bool(self._units))
        if error:
            self._show_error(f"Server SSH: {error}")
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        GLib.source_remove(self._poll_id)
        self.icon.props.visible = False
        self.menu.destroy()


def show_ssh_error(message: str) -> None:
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=message,
    )
    dialog.connect("response", lambda widget, _response: widget.destroy())
    dialog.show()


def _on_panel_exit(_fd: int, _condition: GLib.IOCondition) -> bool:
    Gtk.main_quit()
    return False


if __name__ == "__main__":
    tray = SSHServerTray(show_ssh_error, int(sys.argv[1]))
    # Closing AI-bar also closes this pipe, even if its process crashes.
    GLib.io_add_watch(sys.stdin.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_HUP, _on_panel_exit)
    Gtk.main()
    tray.close()
