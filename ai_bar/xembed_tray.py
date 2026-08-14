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
TRAY_BACKGROUND_RGB = (0x24 * 257, 0x28 * 257, 0x29 * 257)
TRAY_COLOR_VALUES = [
    0xF2 * 257,
    0xF2 * 257,
    0xEE * 257,
    0xE0 * 257,
    0x5D * 257,
    0x5D * 257,
    0xF0 * 257,
    0xC6 * 257,
    0x74 * 257,
    0x63 * 257,
    0xB6 * 257,
    0x8E * 257,
]


class XEmbedTrayHost:
    def __init__(self, container: Gtk.FlowBox, icon_size: int = 24) -> None:
        self.container = container
        self.icon_size = icon_size
        self.display: Any = None
        self.owner: Any = None
        self.icon_background_pixel: int | None = None
        self.selection_atom: int | None = None
        self.opcode_atom: int | None = None
        self.sockets: dict[int, Gtk.Widget] = {}
        self.flow_children: dict[int, Gtk.FlowBoxChild] = {}
        self.poll_id: int | None = None
        self.enabled = False

    def start(self) -> bool:
        if X is None or display is None or protocol is None:
            print("ai-bar: python-xlib non disponibile, host tray XEmbed disattivato.", file=sys.stderr)
            return False

        try:
            self.display = display.Display()
            screen_num = self.display.get_default_screen()
            screen = self.display.screen(screen_num)
            self.icon_background_pixel = screen.default_colormap.alloc_color(*TRAY_BACKGROUND_RGB).pixel
            self.selection_atom = self.display.intern_atom(f"_NET_SYSTEM_TRAY_S{screen_num}")
            self.opcode_atom = self.display.intern_atom("_NET_SYSTEM_TRAY_OPCODE")

            current_owner = self.display.get_selection_owner(self.selection_atom)
            if getattr(current_owner, "id", 0):
                print("ai-bar: un altro tray XEmbed e' gia' attivo.", file=sys.stderr)
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
            self.owner.set_wm_name("ai-bar tray owner")
            self._publish_manager_hints()

            self.owner.set_selection_owner(self.selection_atom, X.CurrentTime)
            if self.display.get_selection_owner(self.selection_atom).id != self.owner.id:
                print("ai-bar: impossibile registrare l'host tray XEmbed.", file=sys.stderr)
                return False

            self._send_manager_announcement(screen.root)
            self.display.flush()
            self.poll_id = GLib.timeout_add(100, self._poll_events)
            self.enabled = True
            return True
        except Exception as exc:
            print(f"ai-bar: host tray XEmbed disattivato: {exc}", file=sys.stderr)
            return False

    def stop(self) -> None:
        if self.poll_id is not None:
            GLib.source_remove(self.poll_id)
            self.poll_id = None

        for xid, cell in list(self.sockets.items()):
            self.sockets.pop(xid, None)
            child = self.flow_children.pop(xid, None)
            if child is not None:
                child.destroy()
            else:
                cell.destroy()

        if self.display is not None and self.owner is not None:
            try:
                self.owner.destroy()
                self.display.flush()
            except Exception:
                pass
        self.owner = None

        self.enabled = False

    def _publish_manager_hints(self) -> None:
        cardinal_atom = self.display.intern_atom("CARDINAL")
        orientation_atom = self.display.intern_atom("_NET_SYSTEM_TRAY_ORIENTATION")
        horizontal = 0
        self.owner.change_property(orientation_atom, cardinal_atom, 32, [horizontal])
        self.owner.change_property(
            self.display.intern_atom("_NET_SYSTEM_TRAY_COLORS"),
            cardinal_atom,
            32,
            TRAY_COLOR_VALUES,
        )
        self.owner.change_property(self.display.intern_atom("_NET_SYSTEM_TRAY_PADDING"), cardinal_atom, 32, [4])
        self.owner.change_property(
            self.display.intern_atom("_NET_SYSTEM_TRAY_ICON_SIZE"),
            cardinal_atom,
            32,
            [self.icon_size],
        )

    def _send_manager_announcement(self, root: Any) -> None:
        manager_atom = self.display.intern_atom("MANAGER")
        event = protocol.event.ClientMessage(
            window=root,
            client_type=manager_atom,
            data=(32, [X.CurrentTime, self.selection_atom, self.owner.id, 0, 0]),
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
        self._apply_icon_background(xid)

        cell = Gtk.EventBox()
        cell.get_style_context().add_class("tray-icon-cell")
        cell.set_direction(Gtk.TextDirection.LTR)
        cell.set_halign(Gtk.Align.CENTER)
        cell.set_visible_window(True)
        cell.set_size_request(self.icon_size + 8, self.icon_size + 8)

        socket = Gtk.Socket()
        socket.set_size_request(self.icon_size, self.icon_size)
        socket.connect("plug-removed", self._on_plug_removed, xid)
        cell.add(socket)
        self.container.insert(cell, -1)
        flow_child = cell.get_parent()
        cell.show_all()

        try:
            socket.add_id(xid)
        except Exception as exc:
            print(f"ai-bar: impossibile agganciare tray icon {xid}: {exc}", file=sys.stderr)
            flow_child.destroy()
            return
        if socket.get_plug_window() is None:
            flow_child.destroy()
            return

        self.sockets[xid] = cell
        self.flow_children[xid] = flow_child
        self.container.show_all()

    def _apply_icon_background(self, xid: int) -> None:
        if self.display is None or self.icon_background_pixel is None:
            return

        try:
            window = self.display.create_resource_object("window", xid)
            window.change_attributes(background_pixel=self.icon_background_pixel)
            window.clear_area()
            self.display.flush()
        except Exception:
            pass

    def _on_plug_removed(self, socket: Gtk.Socket, xid: int) -> bool:
        cell = self.sockets.pop(xid, None)
        child = self.flow_children.pop(xid, None)
        if child is not None:
            child.destroy()
        elif cell is not None:
            cell.destroy()
        return True
