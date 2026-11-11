"""SSH service control for the panel's system tools."""

import subprocess
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk


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


class SSHServerButton(Gtk.Button):
    def __init__(self, show_error: Callable[[str], None]) -> None:
        super().__init__()
        self._show_error = show_error
        self._closed = False
        self._busy = False
        self._active = False
        self._units: tuple[str, ...] = ()
        self.get_style_context().add_class("status-button")
        self.set_relief(Gtk.ReliefStyle.NONE)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        content.pack_start(
            Gtk.Image.new_from_icon_name("network-server-symbolic", Gtk.IconSize.BUTTON),
            False, False, 0,
        )
        content.pack_start(Gtk.Label(label="SSH"), False, False, 0)
        self.add(content)
        self.set_tooltip_text("Server SSH: verifica dello stato…")
        self.connect("clicked", self._toggle)
        self.connect("destroy", self._stop)
        self._poll_id = GLib.timeout_add_seconds(5, self._refresh)
        self._refresh()

    def _refresh(self) -> bool:
        if self._closed:
            return False
        if not self._busy:
            self._start_work(None)
        return True

    def _toggle(self, _button: Gtk.Button) -> None:
        if self._closed or self._busy or not self._units:
            return
        self.set_tooltip_text("Server SSH: operazione in corso…")
        self._start_work(not self._active)

    def _start_work(self, active: bool | None) -> None:
        self._busy = True
        self.set_sensitive(False)
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
        context = self.get_style_context()
        if self._active:
            context.add_class("ssh-active")
        else:
            context.remove_class("ssh-active")
        self.set_sensitive(bool(self._units))
        if state is None:
            tooltip = "Server SSH: stato non disponibile"
        elif not self._units:
            tooltip = "Server SSH non installato: installa openssh-server"
        else:
            tooltip = "Server SSH attivo · Disattiva" if self._active else "Server SSH disattivato · Attiva"
        self.set_tooltip_text(tooltip)
        if error:
            self._show_error(f"Server SSH: {error}")
        return False

    def _stop(self, _button: Gtk.Button) -> None:
        self._closed = True
        GLib.source_remove(self._poll_id)
