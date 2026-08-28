import unittest

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ai_bar.xembed_tray import TRAY_BACKGROUND_RGB, XEmbedIconBin, XEmbedTrayHost


class XEmbedTrayHostTests(unittest.TestCase):
    def test_icon_bin_clamps_icons_that_request_more_space(self):
        window = Gtk.OffscreenWindow()
        cell = Gtk.EventBox()
        cell.set_size_request(24, 24)
        icon_bin = XEmbedIconBin(16)
        oversized_icon = Gtk.DrawingArea()
        oversized_icon.set_size_request(40, 32)
        icon_bin.add(oversized_icon)
        cell.add(icon_bin)
        window.add(cell)
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration()

        bin_allocation = icon_bin.get_allocation()
        allocation = oversized_icon.get_allocation()

        self.assertEqual((bin_allocation.width, bin_allocation.height), (16, 16))
        self.assertEqual((allocation.width, allocation.height), (16, 16))
        window.destroy()

    def test_icon_background_matches_panel(self):
        self.assertEqual(
            TRAY_BACKGROUND_RGB,
            (0x15 * 257, 0x18 * 257, 0x19 * 257),
        )

    def test_default_icons_are_compact(self):
        host = XEmbedTrayHost(container=object())

        self.assertEqual(host.icon_size, 16)

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
