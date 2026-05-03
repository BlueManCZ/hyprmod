"""Searchable picker for installed ``.desktop`` apps.

A modal dialog that lists every installed app from ``Gio.AppInfo``,
filterable by an inline search entry, and calls ``on_pick(app)`` when
the user activates a row. Built once and reused by Autostart (and,
later, env / window-rule pages that may want to reference an app).
"""

from collections.abc import Callable
from html import escape as html_escape

from gi.repository import Adw, Gtk

from hyprmod.core.desktop_apps import DesktopApp, list_apps
from hyprmod.ui import clear_children
from hyprmod.ui.dialog import SingletonDialogMixin
from hyprmod.ui.empty_state import EmptyState


class AppPickerDialog(SingletonDialogMixin, Adw.Dialog):
    """Modal dialog for picking an installed ``.desktop`` app.

    Parameters
    ----------
    on_pick:
        Called with the selected ``DesktopApp`` when the user activates a
        row. The dialog closes itself before invoking the callback so the
        caller can safely present another dialog without z-order issues.

    Open via :meth:`SingletonDialogMixin.present_singleton` rather than
    constructing directly — that path collapses rapid double-clicks
    on the trigger button into a single dialog.
    """

    def __init__(self, *, on_pick: Callable[[DesktopApp], None]):
        super().__init__()
        self._on_pick = on_pick
        self._apps: list[DesktopApp] = list_apps()
        self._filter_term: str = ""
        # Cached row widgets — rebuilt only when the filter changes shape.
        self._list_box: Gtk.ListBox

        self.set_title("Pick an Application")
        self.set_content_width(440)
        self.set_content_height(560)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search applications…")
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

        # Empty-state placeholder shown when filter matches nothing.
        self._empty = EmptyState(
            title="No Matches",
            description="Try a different search term.",
            icon_name="system-search-symbolic",
        )
        self._empty.set_visible(False)
        body.append(self._empty)

        toolbar.set_content(body)
        self.set_child(toolbar)

        self._populate()
        # Focus the search entry so the user can start typing immediately —
        # they almost certainly know the app name and don't want to scroll.
        self._search_entry.grab_focus()

    # ── Population ──

    def _populate(self) -> None:
        clear_children(self._list_box)
        for app in self._apps:
            self._list_box.append(self._make_row(app))
        # The filter is evaluated lazily by GtkListBox; force it to run
        # so the empty-state visibility is right on first show.
        self._list_box.invalidate_filter()
        self._update_empty_state()

    def _make_row(self, app: DesktopApp) -> Gtk.ListBoxRow:
        row = Adw.ActionRow(
            title=html_escape(app.name),
            subtitle=html_escape(app.description or app.command),
        )
        row.set_activatable(True)

        if app.icon_name:
            icon = Gtk.Image.new_from_icon_name(app.icon_name)
            icon.set_pixel_size(32)
            row.add_prefix(icon)

        row.connect("activated", lambda _r, a=app: self._on_row_activated(a))

        # Stash the app on the row so the filter can read it without a
        # parallel dict lookup.
        row.set_name(app.id)
        # Cache for the filter — Adw.ActionRow doesn't have a clean way
        # to attach arbitrary data, so we set a Python attribute. Works
        # because Python wrappers persist for the lifetime of the row.
        row.app_data = app  # type: ignore[attr-defined]
        return row

    # ── Filtering ──

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._filter_term = entry.get_text().strip().lower()
        self._list_box.invalidate_filter()
        self._update_empty_state()

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        if not self._filter_term:
            return True
        app: DesktopApp | None = getattr(row, "app_data", None)
        if app is None:
            return False
        # Substring match across name + description + command. Cheap
        # enough at ~few hundred entries that we don't need a fancier
        # fuzzy matcher — that's a follow-up if usage grows.
        haystack = f"{app.name}\n{app.description}\n{app.command}".lower()
        return self._filter_term in haystack

    def _update_empty_state(self) -> None:
        # Walk the list once to see if anything is currently visible.
        any_visible = False
        child = self._list_box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ListBoxRow) and self._filter_row(child):
                any_visible = True
                break
            child = child.get_next_sibling()
        self._empty.set_visible(not any_visible)
        self._list_box.set_visible(any_visible)

    # ── Selection ──

    def _on_row_activated(self, app: DesktopApp) -> None:
        # Close before calling the handler so a follow-up dialog (the
        # AutostartEditDialog) isn't fighting for focus with this one.
        self.close()
        self._on_pick(app)


__all__ = ["AppPickerDialog"]
