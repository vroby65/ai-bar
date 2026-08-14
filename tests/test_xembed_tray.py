import unittest

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ai_bar.xembed_tray import XEmbedTrayHost


class XEmbedTrayHostTests(unittest.TestCase):
    def test_failed_dock_does_not_leave_an_empty_flow_child(self):
        window = Gtk.Window()
        flow = Gtk.FlowBox()
        window.add(flow)
        window.show_all()
        host = XEmbedTrayHost(flow)

        host._dock_icon(0x7FFFFFFE)

        self.assertEqual(flow.get_children(), [])
        self.assertEqual(host.sockets, {})
        self.assertEqual(host.flow_children, {})
        window.destroy()


if __name__ == "__main__":
    unittest.main()
