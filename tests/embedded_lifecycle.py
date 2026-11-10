"""Run the real foreign-window lifecycle in an isolated GTK process."""

import sys
from pathlib import Path
from unittest.mock import Mock

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
from Xlib import X, display

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_bar.app import AiBarWindow


def drain():
    loop = GLib.MainLoop()
    GLib.timeout_add(100, lambda: (loop.quit(), False)[1])
    loop.run()

window = AiBarWindow.__new__(AiBarWindow)
Gtk.Window.__init__(window)
window.terminals = {}
window.embedded = {}
window.embedded_window_xids = {}
window.detached = {}
window.launcher_buttons = {}
window.detach_button = None
window.reload_button = None
window._place_detached = Mock()
notebook = Gtk.Notebook()
window.terminal_notebook = notebook
window.add(notebook)
key = "window:test"
socket = window._build_embedded_socket(key)
window.embedded[key] = socket
notebook.append_page(socket, Gtk.Label(label="Test"))
window.show_all()
client = display.Display()
foreign = client.screen().root.create_window(0, 0, 200, 200, 0, X.CopyFromParent)
foreign.map()
client.sync()
window.embedded_window_xids[key] = foreign.id
socket.add_id(foreign.id)
drain()
original_socket_xid = socket.get_id()
for _ in range(3):
    window._detach_current_page()
    drain()
    assert window.embedded[key] is socket
    assert socket.get_id() == original_socket_xid
    assert foreign.query_tree().parent.id == original_socket_xid
    assert window.embedded_window_xids[key] == foreign.id
    assert client.get_input_focus().focus.id == foreign.id
    if _ == 1:
        window._on_detached_closed(window.detached[key], None, key)
    else:
        window._reattach(key, window.detached[key])
    drain()
    assert window.embedded[key] is socket
    assert foreign.query_tree().parent.id == original_socket_xid
    assert notebook.get_nth_page(notebook.get_current_page()) is socket
    assert not window.detached
    assert client.get_input_focus().focus.id == foreign.id
foreign.destroy()
client.sync()
drain()
window.destroy()
client.close()
