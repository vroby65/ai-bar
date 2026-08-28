import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, Gtk

from ai_bar.xapp_tray import (
    XAppStatusIconButton,
    XAppStatusIconHost,
    icon_key,
    menu_anchor_for_side,
    panel_position_for_side,
)


class FakeMonitor:
    def __init__(self):
        self.handlers = {}
        self.disconnected = []

    def connect(self, signal, callback):
        handler_id = len(self.handlers) + 1
        self.handlers[handler_id] = (signal, callback)
        return handler_id

    def disconnect(self, handler_id):
        self.disconnected.append(handler_id)

    def list_icons(self):
        return []


class XAppStatusIconHostTests(unittest.TestCase):
    def test_primary_click_forwards_press_and_release(self):
        proxy = Mock()
        proxy.props = SimpleNamespace(
            icon_name="mintupdate-updates-available-symbolic",
            label="",
            tooltip_text="1 aggiornamento disponibile",
            visible=True,
        )
        proxy.connect.side_effect = [1, 2, 3, 4]
        button = XAppStatusIconButton(proxy, 16, "left")

        button.emit("clicked")

        self.assertEqual(proxy.call_button_press.call_count, 1)
        self.assertEqual(proxy.call_button_release.call_count, 1)
        self.assertEqual(proxy.call_button_press.call_args.args[2], Gdk.BUTTON_PRIMARY)
        self.assertEqual(proxy.call_button_release.call_args.args[2], Gdk.BUTTON_PRIMARY)
        button.close()
        button.destroy()

    def test_default_icons_are_compact(self):
        host = XAppStatusIconHost(container=object())

        self.assertEqual(host.icon_size, 16)

    def test_hidden_icon_stays_hidden_when_tray_is_shown(self):
        class FakeProxy:
            def __init__(self):
                self.handlers = {}
                self.props = SimpleNamespace(
                    icon_name=" ", label="", tooltip_text="", visible=False
                )
                self.next_handler_id = 1

            def get_name(self):
                return "org.x.StatusIcon.hidden"

            def get_object_path(self):
                return "/org/x/StatusIcon/hidden"

            def connect(self, signal, callback):
                handler_id = self.next_handler_id
                self.next_handler_id += 1
                self.handlers[handler_id] = (signal, callback)
                return handler_id

            def disconnect(self, handler_id):
                self.handlers.pop(handler_id)

            def set_visible(self, visible):
                self.props.visible = visible
                for signal, callback in self.handlers.values():
                    if signal == "notify::visible":
                        callback(self, None)

        proxy = FakeProxy()
        flow = Gtk.FlowBox()
        host = XAppStatusIconHost(flow)
        host._on_icon_added(None, proxy)
        button = next(iter(host.buttons.values()))
        flow_child = next(iter(host.flow_children.values()))

        self.assertTrue(button.image.get_visible())
        flow.show_all()
        self.assertFalse(button.get_visible())

        self.assertFalse(flow_child.get_visible())
        proxy.set_visible(True)
        self.assertTrue(button.get_visible())
        self.assertTrue(flow_child.get_visible())
        proxy.set_visible(False)
        self.assertFalse(button.get_visible())
        self.assertFalse(flow_child.get_visible())

        host.stop()
        flow.destroy()

    def test_start_registers_xapp_monitor_until_stop(self):
        monitor = FakeMonitor()
        xapp = SimpleNamespace(StatusIconMonitor=lambda: monitor)
        host = XAppStatusIconHost(container=object())

        with patch("ai_bar.xapp_tray.XApp", xapp):
            self.assertTrue(host.start())

        self.assertIs(host.monitor, monitor)
        self.assertEqual(
            [signal for signal, _callback in monitor.handlers.values()],
            ["icon-added", "icon-removed"],
        )

        host.stop()

        self.assertEqual(monitor.disconnected, [1, 2])
        self.assertIsNone(host.monitor)

    def test_icon_key_combines_bus_name_and_object_path(self):
        proxy = SimpleNamespace(
            get_name=lambda: "org.x.StatusIcon.demo",
            get_object_path=lambda: "/org/x/StatusIcon/demo",
        )

        self.assertEqual(
            icon_key(proxy),
            "org.x.StatusIcon.demo/org/x/StatusIcon/demo",
        )

    def test_panel_position_opens_menus_toward_the_desktop(self):
        self.assertEqual(panel_position_for_side("left"), Gtk.PositionType.LEFT)
        self.assertEqual(panel_position_for_side("right"), Gtk.PositionType.RIGHT)
        self.assertEqual(
            menu_anchor_for_side("left", 10, 20, 30),
            (40, 20, Gtk.PositionType.LEFT),
        )
        self.assertEqual(
            menu_anchor_for_side("right", 10, 20, 30),
            (10, 20, Gtk.PositionType.RIGHT),
        )

    def test_icons_are_independent_flow_children_and_are_removed_cleanly(self):
        class FakeStatusButton(Gtk.Button):
            def close(self):
                pass

        proxy = SimpleNamespace(
            get_name=lambda: "org.x.StatusIcon.demo",
            get_object_path=lambda: "/org/x/StatusIcon/demo",
            props=SimpleNamespace(visible=True),
        )
        flow = Gtk.FlowBox()
        host = XAppStatusIconHost(flow)

        with patch(
            "ai_bar.xapp_tray.XAppStatusIconButton",
            return_value=FakeStatusButton(),
        ):
            host._on_icon_added(None, proxy)

        self.assertEqual(len(flow.get_children()), 1)
        self.assertIsInstance(flow.get_children()[0], Gtk.FlowBoxChild)

        host._on_icon_removed(None, proxy)

        self.assertEqual(flow.get_children(), [])


if __name__ == "__main__":
    unittest.main()
