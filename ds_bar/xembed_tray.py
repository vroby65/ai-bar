from __future__ import annotations

import sys
from typing import Any

from gi.repository import GLib, Gtk

try:
    from Xlib import X, display, protocol
except Exception:  # pragma: no cover - exercised only on systems without python-xlib.
    X = None
    display = None
    protocol = None


SYSTEM_TRAY_REQUEST_DOCK = 0


class XEmbedTrayHost:
    def __init__(self, container: Gtk.Box, icon_size: int = 24) -> None:
        self.container = container
        self.icon_size = icon_size
        self.display: Any = None
        self.owner: Any = None
        self.selection_atom: int | None = None
        self.opcode_atom: int | None = None
        self.sockets: dict[int, Gtk.Socket] = {}
        self.poll_id: int | None = None
        self.enabled = False

    def start(self) -> bool:
        if X is None or display is None or protocol is None:
            print("ds-bar: python-xlib non disponibile, host tray XEmbed disattivato.", file=sys.stderr)
            return False

        try:
            self.display = display.Display()
            screen_num = self.display.get_default_screen()
            screen = self.display.screen(screen_num)
            self.selection_atom = self.display.intern_atom(f"_NET_SYSTEM_TRAY_S{screen_num}")
            self.opcode_atom = self.display.intern_atom("_NET_SYSTEM_TRAY_OPCODE")

            current_owner = self.display.get_selection_owner(self.selection_atom)
            if getattr(current_owner, "id", 0):
                print("ds-bar: un altro tray XEmbed e' gia' attivo.", file=sys.stderr)
                return False

            self.owner = screen.root.create_window(
                -1,
                -1,
                1,
                1,
                0,
                X.CopyFromParent,
                X.InputOutput,
                X.CopyFromParent,
                event_mask=X.StructureNotifyMask,
            )
            self.owner.set_wm_name("ds-bar tray owner")
            self._publish_orientation()

            self.display.set_selection_owner(self.owner, self.selection_atom, X.CurrentTime)
            if self.display.get_selection_owner(self.selection_atom).id != self.owner.id:
                print("ds-bar: impossibile registrare l'host tray XEmbed.", file=sys.stderr)
                return False

            self._send_manager_announcement(screen.root)
            self.display.flush()
            self.poll_id = GLib.timeout_add(100, self._poll_events)
            self.enabled = True
            return True
        except Exception as exc:
            print(f"ds-bar: host tray XEmbed disattivato: {exc}", file=sys.stderr)
            return False

    def stop(self) -> None:
        if self.poll_id is not None:
            GLib.source_remove(self.poll_id)
            self.poll_id = None

        for socket in list(self.sockets.values()):
            socket.destroy()
        self.sockets.clear()

        if self.display is not None and self.selection_atom is not None:
            try:
                self.display.set_selection_owner(X.NONE, self.selection_atom, X.CurrentTime)
                self.display.flush()
            except Exception:
                pass

        self.enabled = False

    def _publish_orientation(self) -> None:
        orientation_atom = self.display.intern_atom("_NET_SYSTEM_TRAY_ORIENTATION")
        cardinal_atom = self.display.intern_atom("CARDINAL")
        horizontal = 0
        self.owner.change_property(orientation_atom, cardinal_atom, 32, [horizontal])

    def _send_manager_announcement(self, root: Any) -> None:
        manager_atom = self.display.intern_atom("MANAGER")
        event = protocol.event.ClientMessage(
            window=root,
            client_type=manager_atom,
            data=(32, [X.CurrentTime, manager_atom, self.selection_atom, self.owner.id, 0]),
        )
        root.send_event(event, event_mask=X.StructureNotifyMask)

    def _poll_events(self) -> bool:
        if self.display is None:
            return False

        while self.display.pending_events():
            event = self.display.next_event()
            self._handle_event(event)
        return True

    def _handle_event(self, event: Any) -> None:
        if event.type != X.ClientMessage or event.client_type != self.opcode_atom:
            return

        data = event.data[1]
        opcode = data[1]
        if opcode != SYSTEM_TRAY_REQUEST_DOCK:
            return

        xid = int(data[2])
        if xid and xid not in self.sockets:
            self._dock_icon(xid)

    def _dock_icon(self, xid: int) -> None:
        socket = Gtk.Socket()
        socket.set_size_request(self.icon_size, self.icon_size)
        socket.connect("plug-removed", self._on_plug_removed, xid)
        self.container.pack_start(socket, False, False, 0)
        socket.show()

        try:
            socket.add_id(xid)
        except Exception as exc:
            print(f"ds-bar: impossibile agganciare tray icon {xid}: {exc}", file=sys.stderr)
            socket.destroy()
            return

        self.sockets[xid] = socket
        self.container.show_all()

    def _on_plug_removed(self, socket: Gtk.Socket, xid: int) -> bool:
        self.sockets.pop(xid, None)
        socket.destroy()
        return True
