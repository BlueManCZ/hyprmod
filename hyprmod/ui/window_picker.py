"""Searchable picker for currently-open Hyprland windows.

A modal dialog that lists every mapped window via ``hyprctl clients`` and
calls ``on_pick(window)`` when the user activates a row. Used by the
window-rules edit dialog so users can build a rule by pointing at a
running window instead of having to remember class names or type regex
by hand.

Window metadata comes from ``hyprland_socket.commands.get_windows()``,
which goes straight to the IPC socket. We snapshot once at dialog open
— hot-reloading the list while the user scrolls would steal focus and
re-order rows, which is the kind of thing that makes pickers feel
broken. There's a "Refresh" button for the rare case where the user
launches a window mid-pick.
"""

from collections.abc import Callable
from html import escape as html_escape

from gi.repository import Adw, Gtk
from hyprland_socket import HyprlandError, Window, get_windows

from hyprmod.core.desktop_apps import DesktopApp, list_apps
from hyprmod.ui import clear_children
from hyprmod.ui.dialog import SingletonDialogMixin
from hyprmod.ui.empty_state import EmptyState


class WindowPickerDialog(SingletonDialogMixin, Adw.Dialog):
    """Modal dialog for picking an open Hyprland window.

    Parameters
    ----------
    on_pick:
        Called with the chosen ``Window`` when the user activates a row.
        The dialog closes itself before invoking the callback so the
        caller (typically the rule edit dialog) can immediately update
        its own widgets without z-order fighting.

    Open via :meth:`SingletonDialogMixin.present_singleton` rather than
    constructing directly.
    """

    def __init__(self, *, on_pick: Callable[[Window], None]):
        super().__init__()
        self._on_pick = on_pick
        self._windows: list[Window] = []
        # Match window class against installed apps so the row prefix
        # icon mirrors what the user sees in their app launcher. Cheap
        # to snapshot: ``list_apps()`` walks the .desktop XDG dirs once.
        self._installed_apps: list[DesktopApp] = list_apps()
        self._filter_term: str = ""
        self._list_box: Gtk.ListBox

        self.set_title("Pick an Open Window")
        self.set_content_width(520)
        self.set_content_height(560)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        # Refresh re-queries Hyprland's IPC; useful if the user opens a
        # window after the picker is already up.
        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Re-query open windows")
        refresh_btn.connect("clicked", lambda _b: self._refresh())
        header.pack_end(refresh_btn)
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search by class or title…")
        self._search_entry.connect("search-changed", self._on_search_changed)
        body.append(self._search_entry)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_filter_func(self._filter_row)
        scrolled.set_child(self._list_box)
        body.append(scrolled)

        # Empty-state for "no windows open" and "filter matches nothing".
        # Two states reuse one widget — the title/description swaps based
        # on which condition triggered visibility.
        self._empty = EmptyState(
            title="No Open Windows",
            description="Open a window in Hyprland and try again.",
            icon_name="window-symbolic",
        )
        self._empty.set_visible(False)
        body.append(self._empty)

        toolbar.set_content(body)
        self.set_child(toolbar)

        self._refresh()
        self._search_entry.grab_focus()

    # ── Population ──

    def _refresh(self) -> None:
        """Re-query Hyprland and rebuild the list."""
        try:
            windows = get_windows()
        except HyprlandError:
            # IPC can fail (Hyprland down, socket gone, JSON shape
            # surprise). Silently fall back to an empty list — the
            # empty-state copy already explains "no windows" to the user.
            windows = []
        # Hide unmapped windows (e.g. about-to-close, special workspace
        # offscreen) — they have empty titles and would bloat the list
        # without giving the user anything to point at.
        self._windows = [w for w in windows if w.mapped and (w.class_name or w.title)]
        self._populate()

    def _populate(self) -> None:
        clear_children(self._list_box)
        for window in self._windows:
            self._list_box.append(self._make_row(window))
        self._list_box.invalidate_filter()
        self._update_empty_state()

    def _make_row(self, window: Window) -> Gtk.ListBoxRow:
        # Title: the window's *current* title is what the user sees
        # right now, so it's the most recognisable identity. Fall back
        # to class when the window has no title (transient dialogs).
        display_title = window.title or window.class_name or "(untitled)"
        # Subtitle: surface class + workspace so two windows from the
        # same app on different workspaces are distinguishable.
        subtitle_parts: list[str] = []
        if window.class_name:
            subtitle_parts.append(window.class_name)
        if window.workspace_name:
            subtitle_parts.append(f"workspace {window.workspace_name}")
        elif window.workspace_id >= 0:
            subtitle_parts.append(f"workspace {window.workspace_id}")
        if window.xwayland:
            subtitle_parts.append("XWayland")
        subtitle = " · ".join(subtitle_parts)

        row = Adw.ActionRow(
            title=html_escape(display_title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.set_activatable(True)

        icon = self._resolve_icon(window)
        if icon is not None:
            row.add_prefix(icon)

        row.connect("activated", lambda _r, w=window: self._on_row_activated(w))

        # Stash the window dataclass for the filter — keeps the filter
        # callback decoupled from list-box index lookups.
        row.window_data = window  # type: ignore[attr-defined]
        return row

    def _resolve_icon(self, window: Window) -> Gtk.Image | None:
        """Best-effort icon for *window* by matching class against installed apps."""
        # Class matching is intentionally loose: try exact first, then
        # case-insensitive, then prefix. Hyprland's class strings match
        # WM_CLASS conventions (e.g. ``firefox``, ``org.kde.dolphin``)
        # which usually align with the .desktop file's ``StartupWMClass``
        # but we don't have that exposed in DesktopApp — substring is
        # the next-best thing.
        class_name = window.class_name.strip()
        if not class_name:
            return None
        lower = class_name.lower()
        for app in self._installed_apps:
            if not app.icon_name:
                continue
            if app.id and app.id.lower().startswith(lower):
                return Gtk.Image.new_from_icon_name(app.icon_name)
            if app.command and lower in app.command.lower():
                return Gtk.Image.new_from_icon_name(app.icon_name)
        # Generic window icon as a fallback so rows align visually.
        fallback = Gtk.Image.new_from_icon_name("window-symbolic")
        fallback.set_opacity(0.6)
        return fallback

    # ── Filtering ──

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._filter_term = entry.get_text().strip().lower()
        self._list_box.invalidate_filter()
        self._update_empty_state()

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        if not self._filter_term:
            return True
        window: Window | None = getattr(row, "window_data", None)
        if window is None:
            return False
        haystack = (
            f"{window.class_name}\n{window.title}\n{window.initial_class}\n{window.initial_title}"
        ).lower()
        return self._filter_term in haystack

    def _update_empty_state(self) -> None:
        any_visible = False
        child = self._list_box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ListBoxRow) and self._filter_row(child):
                any_visible = True
                break
            child = child.get_next_sibling()

        self._list_box.set_visible(any_visible)
        if any_visible:
            self._empty.set_visible(False)
            return

        # Two empty paths share the widget; pick copy that explains why.
        if not self._windows:
            self._empty.set_title("No Open Windows")
            self._empty.set_description(
                "Open a window in Hyprland (or click refresh) to pick from it."
            )
        else:
            self._empty.set_title("No Matches")
            self._empty.set_description("Try a different search term.")
        self._empty.set_visible(True)

    # ── Selection ──

    def _on_row_activated(self, window: Window) -> None:
        # Close before invoking on_pick so the rule dialog (the typical
        # caller) can immediately re-grab focus on its own fields.
        self.close()
        self._on_pick(window)


__all__ = ["WindowPickerDialog"]
