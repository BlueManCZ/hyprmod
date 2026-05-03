"""Sidebar navigation pane with task-oriented categories and toggleable search."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.ui.icons import (
    AUTOSTART_ICON,
    BINDS_ICON,
    ENV_VARS_ICON,
    LAYER_RULES_ICON,
    LAYOUTS_ICON,
    MONITORS_ICON,
    PROFILES_ICON,
    SETTINGS_ICON,
    WINDOW_RULES_ICON,
)


class SidebarRow(Adw.ActionRow):
    """Sidebar navigation row with a typed group identifier."""

    def __init__(self, group_id: str, **kwargs):
        super().__init__(**kwargs)
        self.group_id = group_id
        self._badge = Gtk.Label()
        self._badge.add_css_class("sidebar-badge")
        self._badge.set_visible(False)
        self._badge.set_halign(Gtk.Align.CENTER)
        self._badge.set_valign(Gtk.Align.CENTER)
        self.add_suffix(self._badge)

    def set_badge_count(self, count: int):
        """Show or hide the pending-changes badge."""
        if count > 0:
            self._badge.set_label(str(count))
            self._badge.set_visible(True)
        else:
            self._badge.set_visible(False)


class Sidebar:
    """Builds and manages the sidebar navigation pane.

    The sidebar is organised by user task rather than by ``hyprland.conf``
    section: *Look & Feel*, *Input*, *Display*, *Window Management*,
    *Startup*, *Advanced*. Profiles and Settings are pinned at the bottom;
    Pending Changes lives in each page's header bar as a chip rather than
    in the navigation list, so the badge is visible from anywhere.

    The search entry hides by default and slides in below the header when
    the toolbar's search button is toggled (or Ctrl+F is pressed). Keeps
    the categories close to the top of the sidebar — searching is the
    sometimes path, browsing is the everyday one.

    Parameters:
        on_page_selected: Called with the group_id when a sidebar row is selected.
        on_search_changed: Connected to the search entry's ``search-changed`` signal.
        on_search_activate: Connected to the search entry's ``activate`` signal.
        on_search_dismissed: Called when the search button is toggled off, the
            entry's stop-search fires, or :meth:`clear_search` is invoked. The
            window uses it to restore the previously visible page synchronously
            (no need to wait on the 150 ms ``search-changed`` debounce).
    """

    def __init__(
        self,
        *,
        on_page_selected: Callable[[str], None],
        on_search_changed: Callable,
        on_search_activate: Callable,
        on_search_dismissed: Callable[[], None],
    ):
        self._on_page_selected = on_page_selected
        self._on_search_dismissed = on_search_dismissed
        self._rows_by_id: dict[str, SidebarRow] = {}
        self._lists: list[Gtk.ListBox] = []
        self._sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Toggleable search: a button in the sidebar header controls the
        # entry's visibility. The categories sit at the top of the
        # sidebar by default and only get pushed down when the user
        # actually wants to search.
        self.search_button = Gtk.ToggleButton(icon_name="edit-find-symbolic")
        self.search_button.set_tooltip_text("Search options (Ctrl+F)")
        self.search_button.connect("toggled", self._on_toggle_search)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search options…")
        self._search_entry.set_margin_top(8)
        self._search_entry.set_margin_start(8)
        self._search_entry.set_margin_end(8)
        self._search_entry.set_margin_bottom(4)
        self._search_entry.set_visible(False)
        self._search_entry.connect("search-changed", on_search_changed)
        self._search_entry.connect("activate", on_search_activate)
        # Esc inside the entry routes through clear_search, which toggles
        # the button off and triggers the same dismiss path as a click.
        self._search_entry.connect("stop-search", lambda *_: self.clear_search())

        # Build navigation page
        self.nav_page = self._build()

    def _build(self) -> Adw.NavigationPage:
        nav_page = Adw.NavigationPage(title="HyprMod")
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_title(True)
        header.pack_end(self.search_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._search_entry)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._sidebar_box)
        scrolled.set_vexpand(True)
        content.append(scrolled)

        # Pinned utilities below the scrolled area: Profiles and Settings.
        # Pending Changes used to live here but is now a chip in every page
        # header — it's status, not navigation, and the count belongs near
        # the dirty banner where the user acts on it.
        self._pinned_list = Gtk.ListBox()
        self._pinned_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._pinned_list.add_css_class("navigation-sidebar")
        self._pinned_list.connect("row-selected", self._on_row_selected)

        profiles_row = SidebarRow(group_id="profiles", title="Profiles")
        profiles_row.set_activatable(True)
        profiles_row.add_prefix(Gtk.Image.new_from_icon_name(PROFILES_ICON))
        self._pinned_list.append(profiles_row)
        self._rows_by_id["profiles"] = profiles_row

        settings_row = SidebarRow(group_id="settings", title="Settings")
        settings_row.set_activatable(True)
        settings_row.add_prefix(Gtk.Image.new_from_icon_name(SETTINGS_ICON))
        self._pinned_list.append(settings_row)
        self._rows_by_id["settings"] = settings_row

        content.append(self._pinned_list)

        toolbar.set_content(content)
        nav_page.set_child(toolbar)
        return nav_page

    def populate(self, groups_by_id: dict[str, dict]) -> None:
        """Add task-oriented category headers and navigation rows.

        Schema groups whose options are entirely unavailable in the running
        Hyprland version (filtered upstream by ``schema._drop_unavailable``)
        are silently skipped, so e.g. ``scrolling`` won't appear on
        Hyprland < 0.50.
        """

        # Categories attach to the sidebar lazily on first row — so if every
        # schema group in a category was dropped by the version guard (e.g.
        # ``scrolling`` on Hyprland < 0.50), we don't end up with an orphan
        # header floating above no rows.
        pending_headers: dict[Gtk.ListBox, Gtk.Label] = {}

        def new_category(label: str) -> Gtk.ListBox:
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            listbox.add_css_class("navigation-sidebar")
            listbox.connect("row-selected", self._on_row_selected)
            pending_headers[listbox] = self._make_category_label(label)
            return listbox

        def add_row(listbox: Gtk.ListBox, group_id: str, label: str, icon: str | None) -> None:
            header = pending_headers.pop(listbox, None)
            if header is not None:
                self._sidebar_box.append(header)
                self._sidebar_box.append(listbox)
                self._lists.append(listbox)
            row = SidebarRow(group_id=group_id, title=label)
            row.set_activatable(True)
            if icon:
                row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            listbox.append(row)
            self._rows_by_id[group_id] = row

        def add_schema_row(listbox: Gtk.ListBox, group_id: str) -> None:
            # Groups are filtered by the running Hyprland version, so a hard-
            # coded id may not be in ``groups_by_id``. Skip silently — the
            # sidebar only shows pages the compositor can actually configure.
            group = groups_by_id.get(group_id)
            if group is None:
                return
            add_row(listbox, group_id, group["label"], group.get("icon"))

        look_and_feel = new_category("Look & Feel")
        add_schema_row(look_and_feel, "general")
        add_schema_row(look_and_feel, "decoration")
        add_schema_row(look_and_feel, "animations")
        add_schema_row(look_and_feel, "cursor")

        input_cat = new_category("Input")
        add_row(input_cat, "binds", "Keybinds", BINDS_ICON)
        add_schema_row(input_cat, "input")
        add_schema_row(input_cat, "gestures")

        display = new_category("Display")
        add_row(display, "monitors", "Monitors", MONITORS_ICON)

        windowing = new_category("Window Management")
        # Dwindle/Master/Scrolling are merged into a single Layouts page
        # with a ViewSwitcher — see ``pages/layouts.py``. The schema groups
        # are hidden via ``parent_page: "layouts"`` so they still
        # contribute option keys to ``_key_to_group`` (for badges) and
        # remain searchable.
        add_row(windowing, "layouts", "Layouts", LAYOUTS_ICON)
        add_row(windowing, "window_rules", "Window Rules", WINDOW_RULES_ICON)
        add_row(windowing, "layer_rules", "Layer Rules", LAYER_RULES_ICON)

        startup = new_category("Startup")
        add_row(startup, "autostart", "Autostart", AUTOSTART_ICON)
        add_row(startup, "env_vars", "Env Variables", ENV_VARS_ICON)

        advanced = new_category("Advanced")
        add_schema_row(advanced, "xwayland")
        add_schema_row(advanced, "ecosystem")
        add_schema_row(advanced, "misc")

        # Pinned list goes last so select_first() picks schema rows
        self._lists.append(self._pinned_list)

    def select_first(self) -> None:
        """Select the first row in the first list."""
        if self._lists:
            first_row = self._lists[0].get_row_at_index(0)
            if first_row:
                self._lists[0].select_row(first_row)

    def select_row(self, group_id: str) -> None:
        """Select the sidebar row for the given group."""
        row = self._rows_by_id.get(group_id)
        if row:
            parent_list = row.get_parent()
            if isinstance(parent_list, Gtk.ListBox):
                parent_list.select_row(row)

    def deselect_all(self) -> None:
        """Deselect all rows in all sidebar lists."""
        for sl in self._lists:
            sl.unselect_all()

    def get_selected_group_id(self) -> str | None:
        """Return the group_id of the currently selected row, if any."""
        for sl in self._lists:
            row = sl.get_selected_row()
            if isinstance(row, SidebarRow):
                return row.group_id
        return None

    def update_badges(self, counts: dict[str, int]) -> None:
        """Update pending-change count badges on sidebar rows."""
        for group_id, row in self._rows_by_id.items():
            row.set_badge_count(counts.get(group_id, 0))

    # -- Search --

    def focus_search(self) -> None:
        """Reveal and focus the search entry (used by Ctrl+F)."""
        self.search_button.set_active(True)

    def clear_search(self) -> None:
        """Hide the search entry and restore the previously visible page.

        Routes through the toggle button so the entry stays in sync — the
        ``toggled`` handler clears the text, hides the entry, and calls
        ``on_search_dismissed`` synchronously. The synchronous dismiss
        avoids waiting on the 150 ms ``search-changed`` debounce.
        """
        if self.search_button.get_active():
            self.search_button.set_active(False)
        else:
            # Already inactive — call the dismiss callback directly so
            # callers (e.g. clicking a search result) still get the
            # page-restore opt-out path.
            self._on_search_dismissed()

    def _on_toggle_search(self, *_args) -> None:
        if self.search_button.get_active():
            self._search_entry.set_visible(True)
            self._search_entry.grab_focus()
        else:
            if self._search_entry.get_text():
                self._search_entry.set_text("")
            self._search_entry.set_visible(False)
            self._on_search_dismissed()

    # -- Row selection --

    def _on_row_selected(self, listbox, row):
        if isinstance(row, SidebarRow):
            self._on_page_selected(row.group_id)
            for other in self._lists:
                if other is not listbox:
                    other.unselect_all()

    @staticmethod
    def _make_category_label(label: str) -> Gtk.Label:
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("sidebar-category-header")
        return lbl
