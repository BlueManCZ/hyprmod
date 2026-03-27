"""Sidebar navigation pane with grouped rows, badges, and search entry."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.ui.dna import DnaWidget


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

    Parameters:
        on_page_selected: Called with the group_id when a sidebar row is selected.
        on_search_changed: Connected to the search entry's ``search-changed`` signal.
        on_search_activate: Connected to the search entry's ``activate`` signal.
        on_search_stop: Connected to the search entry's ``stop-search`` signal.
        on_search_dismissed: Called when the search toggle is deactivated (button or stop-search).
    """

    def __init__(
        self,
        *,
        on_page_selected: Callable[[str], None],
        on_search_changed: Callable,
        on_search_activate: Callable,
        on_search_stop: Callable,
        on_search_dismissed: Callable[[], None],
    ):
        self._on_page_selected = on_page_selected
        self._on_search_dismissed = on_search_dismissed
        self._rows_by_id: dict[str, SidebarRow] = {}
        self._lists: list[Gtk.ListBox] = []
        self._sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._dna = DnaWidget(width=180, height=28)
        self._dna.set_halign(Gtk.Align.CENTER)
        self._dna.set_margin_top(4)
        self._dna.set_margin_bottom(8)

        # Search widgets — the window wires up the handlers
        self.search_button = Gtk.ToggleButton(icon_name="edit-find-symbolic")
        self.search_button.set_tooltip_text("Search options (Ctrl+F)")

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search options\u2026")
        self._search_entry.set_margin_top(8)
        self._search_entry.set_margin_start(8)
        self._search_entry.set_margin_end(8)
        self._search_entry.set_margin_bottom(4)
        self._search_entry.set_visible(False)
        self._search_entry.connect("search-changed", on_search_changed)
        self._search_entry.connect("activate", on_search_activate)
        self._search_entry.connect("stop-search", on_search_stop)

        # Build navigation page
        self.nav_page = self._build()

    def _build(self) -> Adw.NavigationPage:
        nav_page = Adw.NavigationPage(title="HyprMod")
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_title(True)

        self.search_button.connect("toggled", self._on_toggle_search)
        header.pack_end(self.search_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._search_entry)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._sidebar_box)
        scrolled.set_vexpand(True)
        content.append(scrolled)

        # Profiles list (pinned below the scrolled area)
        self._profiles_list = Gtk.ListBox()
        self._profiles_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._profiles_list.add_css_class("navigation-sidebar")
        self._profiles_list.connect("row-selected", self._on_row_selected)
        self._lists.append(self._profiles_list)
        profiles_row = SidebarRow(group_id="profiles", title="Profiles")
        profiles_row.set_activatable(True)
        profiles_row.add_prefix(Gtk.Image.new_from_icon_name("user-bookmarks-symbolic"))
        self._profiles_list.append(profiles_row)
        content.append(self._profiles_list)

        content.append(self._dna)

        toolbar.set_content(content)
        nav_page.set_child(toolbar)
        return nav_page

    def populate(self, groups_by_id: dict[str, dict]) -> None:
        """Add category headers and navigation rows for schema groups."""

        def new_category(label: str) -> Gtk.ListBox:
            self._sidebar_box.append(self._make_category_label(label))
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            listbox.add_css_class("navigation-sidebar")
            listbox.connect("row-selected", self._on_row_selected)
            self._lists.append(listbox)
            self._sidebar_box.append(listbox)
            return listbox

        def add_row(listbox: Gtk.ListBox, group_id: str, label: str, icon: str | None) -> None:
            row = SidebarRow(group_id=group_id, title=label)
            row.set_activatable(True)
            if icon:
                row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            listbox.append(row)
            self._rows_by_id[group_id] = row

        def add_schema_row(listbox: Gtk.ListBox, group_id: str) -> None:
            group = groups_by_id[group_id]
            add_row(listbox, group_id, group["label"], group.get("icon"))

        appearance = new_category("Appearance")
        add_schema_row(appearance, "general")
        add_schema_row(appearance, "decoration")
        add_schema_row(appearance, "animations")

        input_display = new_category("Input & Display")
        add_schema_row(input_display, "input")
        add_schema_row(input_display, "cursor")
        add_row(input_display, "binds", "Keybinds", "keyboard-shortcuts-symbolic")
        add_schema_row(input_display, "gestures")
        add_row(input_display, "monitors", "Monitors", "display-symbolic")

        layouts = new_category("Layouts")
        add_schema_row(layouts, "dwindle")
        add_schema_row(layouts, "master")

        other = new_category("Other")
        add_schema_row(other, "xwayland")
        add_schema_row(other, "ecosystem")
        add_schema_row(other, "misc")

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

    def update_dna(self, values: dict) -> None:
        """Update the DNA graphic from current live values."""
        self._dna.set_values(values)

    # -- Search toggle --

    def _on_toggle_search(self, *_args):
        if self.search_button.get_active():
            self._search_entry.set_visible(True)
            self._search_entry.grab_focus()
        else:
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
