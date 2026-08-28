from __future__ import annotations

import os
import sys
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp
except (ImportError, ValueError):  # pragma: no cover - depends on the desktop packages.
    XApp = None


def icon_key(proxy: Any) -> str:
    return f"{proxy.get_name()}{proxy.get_object_path()}"


def panel_position_for_side(side: str) -> Gtk.PositionType:
    return Gtk.PositionType.LEFT if side == "left" else Gtk.PositionType.RIGHT


def menu_anchor_for_side(
    side: str, x: int, y: int, width: int
) -> tuple[int, int, Gtk.PositionType]:
    position = panel_position_for_side(side)
    if side == "left":
        return x + width, y, position
    return x, y, position


class XAppStatusIconButton(Gtk.Button):
    def __init__(self, proxy: Any, icon_size: int, panel_side: str) -> None:
        super().__init__()
        self.proxy = proxy
        self.icon_size = icon_size
        self.panel_side = panel_side
        self.proxy_handlers: list[int] = []

        self.set_can_focus(False)
        self.set_focus_on_click(False)
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.get_style_context().add_class("tray-icon-cell")
        self.add_events(Gdk.EventMask.SCROLL_MASK)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.image = Gtk.Image()
        self.label = Gtk.Label(no_show_all=True)
        box.pack_start(self.image, False, False, 0)
        box.pack_start(self.label, False, False, 0)
        self.add(box)
        self.show_all()
        self.set_no_show_all(True)

        try:
            self.proxy.props.icon_size = self.icon_size
        except (AttributeError, GLib.Error):
            pass

        self.proxy_handlers.extend(
            [
                self.proxy.connect("notify::icon-name", self._on_icon_changed),
                self.proxy.connect("notify::label", self._on_label_changed),
                self.proxy.connect("notify::tooltip-text", self._on_tooltip_changed),
                self.proxy.connect("notify::visible", self._on_visible_changed),
            ]
        )
        self.connect("clicked", self._on_primary_clicked)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("scroll-event", self._on_scroll)
        self._sync_from_proxy()

    def close(self) -> None:
        for handler_id in self.proxy_handlers:
            self.proxy.disconnect(handler_id)
        self.proxy_handlers.clear()

    def _sync_from_proxy(self) -> None:
        self._update_icon()
        self._update_label()
        self.set_tooltip_markup(self.proxy.props.tooltip_text or None)
        self.set_visible(bool(self.proxy.props.visible))

    def _update_icon(self) -> None:
        icon_name = self.proxy.props.icon_name
        self.image.set_pixel_size(self.icon_size)
        if not icon_name:
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.MENU)
            return

        try:
            if os.path.exists(icon_name):
                self.image.set_from_gicon(
                    Gio.FileIcon.new(Gio.File.new_for_path(icon_name)),
                    Gtk.IconSize.MENU,
                )
            else:
                self.image.set_from_icon_name(icon_name, Gtk.IconSize.MENU)
        except (GLib.Error, TypeError):
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.MENU)

    def _update_label(self) -> None:
        label = self.proxy.props.label or ""
        self.label.set_text(label)
        self.label.set_visible(bool(label))

    def _on_icon_changed(self, _proxy: Any, _pspec: Any) -> None:
        self._update_icon()

    def _on_label_changed(self, _proxy: Any, _pspec: Any) -> None:
        self._update_label()

    def _on_tooltip_changed(self, _proxy: Any, _pspec: Any) -> None:
        self.set_tooltip_markup(self.proxy.props.tooltip_text or None)

    def _on_visible_changed(self, _proxy: Any, _pspec: Any) -> None:
        visible = bool(self.proxy.props.visible)
        self.set_visible(visible)
        parent = self.get_parent()
        if parent is not None:
            parent.set_visible(visible)

    def _menu_anchor(self) -> tuple[int, int, Gtk.PositionType]:
        window = self.get_window()
        if window is None:
            return menu_anchor_for_side(self.panel_side, 0, 0, 0)

        origin = window.get_origin()
        if len(origin) == 3:
            _success, x, y = origin
        else:
            x, y = origin
        allocation = self.get_allocation()
        return menu_anchor_for_side(
            self.panel_side,
            int(x + allocation.x),
            int(y + allocation.y),
            int(allocation.width),
        )

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == Gdk.BUTTON_PRIMARY:
            return Gdk.EVENT_PROPAGATE

        x, y, position = self._menu_anchor()
        self.proxy.call_button_press(
            x, y, event.button, event.time, position, None, None
        )
        return Gdk.EVENT_STOP

    def _on_button_release(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == Gdk.BUTTON_PRIMARY:
            return Gdk.EVENT_PROPAGATE

        x, y, position = self._menu_anchor()
        self.proxy.call_button_release(
            x, y, event.button, event.time, position, None, None
        )
        return Gdk.EVENT_STOP

    def _on_primary_clicked(self, _button: Gtk.Button) -> None:
        x, y, position = self._menu_anchor()
        event_time = Gtk.get_current_event_time()
        self.proxy.call_button_press(
            x, y, Gdk.BUTTON_PRIMARY, event_time, position, None, None
        )
        self.proxy.call_button_release(
            x, y, Gdk.BUTTON_PRIMARY, event_time, position, None, None
        )

    def _on_scroll(self, _widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        has_direction, direction = event.get_scroll_direction()
        if not has_direction or direction == Gdk.ScrollDirection.SMOOTH:
            return Gdk.EVENT_PROPAGATE

        delta = (
            -1 if direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT) else 1
        )
        self.proxy.call_scroll(delta, int(direction), event.time, None, None)
        return Gdk.EVENT_STOP


class XAppStatusIconHost:
    def __init__(
        self, container: Gtk.FlowBox, icon_size: int = 16, panel_side: str = "left"
    ) -> None:
        self.container = container
        self.icon_size = icon_size
        self.panel_side = panel_side
        self.monitor: Any = None
        self.monitor_handlers: list[int] = []
        self.buttons: dict[str, XAppStatusIconButton] = {}
        self.flow_children: dict[str, Gtk.FlowBoxChild] = {}

    def start(self) -> bool:
        if XApp is None:
            print(
                "ai-bar: gir1.2-xapp-1.0 non disponibile, icone XApp disattivate.",
                file=sys.stderr,
            )
            return False
        if self.monitor is not None:
            return True

        try:
            self.monitor = XApp.StatusIconMonitor()
            self.monitor_handlers = [
                self.monitor.connect("icon-added", self._on_icon_added),
                self.monitor.connect("icon-removed", self._on_icon_removed),
            ]
            for proxy in self.monitor.list_icons():
                self._on_icon_added(self.monitor, proxy)
            return True
        except Exception as exc:
            self.monitor = None
            self.monitor_handlers.clear()
            print(f"ai-bar: monitor XApp disattivato: {exc}", file=sys.stderr)
            return False

    def stop(self) -> None:
        for key, button in list(self.buttons.items()):
            button.close()
            child = self.flow_children.pop(key, None)
            if child is not None:
                child.destroy()
            else:
                button.destroy()
        self.buttons.clear()

        if self.monitor is not None:
            for handler_id in self.monitor_handlers:
                self.monitor.disconnect(handler_id)
        self.monitor_handlers.clear()
        self.monitor = None

    def is_registered(self) -> bool:
        if XApp is None or self.monitor is None:
            return False
        try:
            return bool(XApp.StatusIcon.any_monitors())
        except Exception:
            return False

    def _on_icon_added(self, _monitor: Any, proxy: Any) -> None:
        key = icon_key(proxy)
        if key in self.buttons:
            return

        button = XAppStatusIconButton(proxy, self.icon_size, self.panel_side)
        button.set_direction(Gtk.TextDirection.LTR)
        button.set_halign(Gtk.Align.CENTER)
        self.buttons[key] = button
        self.container.insert(button, -1)
        flow_child = button.get_parent()
        flow_child.set_no_show_all(True)
        self.flow_children[key] = flow_child
        visible = bool(proxy.props.visible)
        button.set_visible(visible)
        flow_child.set_visible(visible)

    def _on_icon_removed(self, _monitor: Any, proxy: Any) -> None:
        key = icon_key(proxy)
        button = self.buttons.pop(key, None)
        if button is not None:
            button.close()
            child = self.flow_children.pop(key, None)
            if child is not None:
                child.destroy()
            else:
                button.destroy()
