"""UI components — widgets, utilities, and layout helpers."""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import cast

from gi.repository import Adw, Gdk, Gtk, Pango

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
    cancel_label: str = "Cancel",
    appearance: Adw.ResponseAppearance = Adw.ResponseAppearance.DESTRUCTIVE,
) -> Adw.AlertDialog:
    """Present a simple confirmation dialog. Calls on_confirm() if accepted.

    Use this for yes/no questions where the only inputs are the two
    response buttons. Form dialogs (with entry rows, live validation,
    or custom focus handling) should build ``Adw.AlertDialog`` directly
    — wrapping them here would obscure the form logic without
    saving meaningful boilerplate.
    """
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", cancel_label)
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


def try_with_toast(
    show_toast: Callable[..., object],
    error_prefix: str,
    action: Callable[[], object],
    *,
    catch: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    timeout: int = 5,
) -> bool:
    """Run *action*; toast and return ``False`` on caught error, else ``True``.

    Consolidates the common shape::

        try:
            self._window.hypr.keyword(...)
            return True
        except HyprlandError as e:
            self._window.show_toast(f"... — {e}", timeout=5)
            return False

    *show_toast* is the bound method to call (typically
    ``window.show_toast``); the helper passes the formatted message
    as the first positional arg and ``timeout`` as a keyword arg, so
    it works with the project's ``show_toast(message, timeout=...)``
    signature. *catch* defaults to ``Exception`` but should usually
    be narrowed to the IPC-specific class (``HyprlandError``).
    """
    try:
        action()
    except catch as e:
        show_toast(f"{error_prefix} — {e}", timeout=timeout)
        return False
    return True


def build_preview_group(
    description: str = "This is the exact line that will be written to your HyprMod config.",
) -> tuple[Adw.PreferencesGroup, Gtk.Label]:
    """Build a labelled "Preview" group containing a monospace label.

    Returns ``(group, label)`` so the caller can keep a reference to the
    label for ``set_text(...)`` calls. The same shape is used by every
    edit dialog that previews the config line it will write.
    """
    group = Adw.PreferencesGroup(title="Preview")
    group.set_description(description)

    frame = Gtk.Frame()
    frame.add_css_class("view")

    label = Gtk.Label()
    label.set_xalign(0)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_selectable(True)
    label.set_margin_top(10)
    label.set_margin_bottom(10)
    label.set_margin_start(12)
    label.set_margin_end(12)
    label.add_css_class("monospace")
    frame.set_child(label)

    group.add(frame)
    return group, label


def make_inline_hint(
    text: str,
    *,
    icon_name: str = "dialog-information-symbolic",
) -> Gtk.Widget:
    """Build the dim-icon + dim-caption hint row used at the top of list pages.

    Same shape used by the autostart, env-vars, window-rules, layer-rules,
    and binds pages: a horizontal box with a faint icon on the left and a
    wrapped, caption-styled label that claims the rest of the row's width
    (so a paragraph wraps at the right boundary instead of its preferred
    narrow width).
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.set_margin_start(4)

    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_opacity(0.5)
    icon.set_valign(Gtk.Align.START)
    box.append(icon)

    label = Gtk.Label(label=text)
    label.set_wrap(True)
    label.set_xalign(0)
    label.set_hexpand(True)
    label.add_css_class("dim-label")
    label.add_css_class("caption")
    box.append(label)
    return box


def display_path(path: Path) -> str:
    """Return *path* as a string with ``$HOME`` collapsed to ``~``.

    Used in user-facing UI strings (source-line previews, external-
    rule provenance) where absolute home paths are noise. Falls back
    to the absolute path verbatim when *path* is outside ``$HOME``.
    """
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
