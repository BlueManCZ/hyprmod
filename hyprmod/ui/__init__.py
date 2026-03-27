"""UI components — widgets, utilities, and layout helpers."""

import functools
from collections.abc import Callable
from typing import cast

from gi.repository import Adw, Gdk, Gtk

from hyprmod.ui.options import OptionRow, create_option_row  # noqa: F401
from hyprmod.ui.row_actions import RowActions  # noqa: F401

# Fallback accent colors used in Cairo drawing (bezier canvas, monitor preview).
# These are used when the widget can't resolve the GTK accent color from CSS.
ACCENT_RGB = (0.34, 0.54, 0.93)
ACTIVE_RGB = (0.93, 0.55, 0.14)


@functools.cache
def get_cursor_grab() -> Gdk.Cursor:
    """Return a cached grab cursor, creating it on first call."""
    return cast(Gdk.Cursor, Gdk.Cursor.new_from_name("grab"))


@functools.cache
def get_cursor_none() -> Gdk.Cursor:
    """Return a cached invisible cursor, creating it on first call."""
    return cast(Gdk.Cursor, Gdk.Cursor.new_from_name("none"))


def clear_children(container: Gtk.Widget) -> None:
    """Remove all children from a GTK container widget."""
    while child := container.get_first_child():
        container.remove(child)  # type: ignore[attr-defined]


def make_page_layout(
    header: Adw.HeaderBar | None = None,
    spacing: int = 24,
) -> tuple[Adw.ToolbarView, Gtk.Box, Gtk.Box, Gtk.ScrolledWindow]:
    """Standard page layout: toolbar + scrollable clamped content.

    Returns (toolbar_view, page_box, content_box, scrolled).
    Insert banners/bars into page_box before the scrolled window with prepend().
    """
    toolbar_view = Adw.ToolbarView()
    toolbar_view.add_top_bar(header or Adw.HeaderBar())

    page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)

    clamp = Adw.Clamp()
    clamp.set_maximum_size(800)
    clamp.set_tightening_threshold(600)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    content_box.set_margin_top(24)
    content_box.set_margin_bottom(24)
    content_box.set_margin_start(12)
    content_box.set_margin_end(12)
    content_box.set_spacing(spacing)

    clamp.set_child(content_box)
    scrolled.set_child(clamp)
    page_box.append(scrolled)
    toolbar_view.set_content(page_box)
    return toolbar_view, page_box, content_box, scrolled


def confirm(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    label: str,
    on_confirm: Callable[[], object],
    *,
    appearance: Adw.ResponseAppearance = Adw.ResponseAppearance.DESTRUCTIVE,
) -> Adw.AlertDialog:
    """Present a simple confirmation dialog. Calls on_confirm() if accepted."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", label)
    dialog.set_response_appearance("confirm", appearance)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog, response):
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)
    return dialog
