"""UI components — widgets, utilities, and layout helpers."""

import functools
from collections.abc import Callable
from typing import Protocol, cast

from gi.repository import Adw, Gdk, Gtk, Pango
from hyprland_config import keyword_to_lua

from hyprmod.core import config
from hyprmod.ui.options import (  # noqa: F401
    KeyboardLayoutsOptionRow,
    OptionRow,
    create_option_row,
)
from hyprmod.ui.row_actions import RowActions  # noqa: F401


class ShowToast(Protocol):
    """Callable shape of :meth:`HyprModWindow.show_toast`.

    Captured as a Protocol so helpers that take a toast function as a
    dependency (controller injections) keep a typed contract instead of
    falling back to ``Callable[..., object]``.
    """

    def __call__(self, message: str, timeout: int = ..., *, copy: bool = ...) -> None: ...


class ShowBugToast(Protocol):
    """Callable shape of :meth:`HyprModWindow.show_bug_toast`.

    Separate from :class:`ShowToast` because the bug variant has no
    ``copy`` knob (the Report button replaces it), carries a ``detail``
    string for the issue title, and takes ``timeout`` keyword-only.
    """

    def __call__(self, message: str, *, detail: str | None = ..., timeout: int = ...) -> None: ...


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
    show_bug_toast: ShowBugToast,
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
            self._window.show_bug_toast(f"... — {e}", timeout=5)
            return False

    *show_bug_toast* is the bound method to call (typically
    ``window.show_bug_toast``); errors caught here are by construction
    IPC roundtrips hyprmod initiated, so the Report button is the right
    affordance. *catch* defaults to ``Exception`` but should usually
    be narrowed to the IPC-specific class (``HyprlandError``).
    """
    try:
        action()
    except catch as e:
        show_bug_toast(f"{error_prefix} — {e}", detail=str(e), timeout=timeout)
        return False
    return True


def format_config_preview(keyword: str, body: str) -> str:
    """Render a config-line preview in the active mode's syntax.

    Lua mode returns the ``hl.*(...)`` snippet that hyprmod will actually
    write (matching what hits disk byte-for-byte). Hyprlang mode returns
    the canonical ``key = value`` line. Falls back to the Hyprlang form
    when *keyword* has no Lua emitter, so previews stay populated for
    keywords that aren't yet translatable.
    """
    if config.is_lua_mode():
        try:
            return keyword_to_lua(keyword, body)
        except ValueError:
            pass
    return f"{keyword} = {body}"


def build_preview_group(
    description: str = "This is what HyprMod will write to your config file.",
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
