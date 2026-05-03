"""Unified Layouts page — Dwindle / Master / Scrolling under one ViewSwitcher.

Hyprland exposes each layout's tunables as a separate schema group, but
users only ever care about one layout at a time (the one they're using),
and the previous one-row-per-layout sidebar treatment was misleading
about the choice's mutual exclusivity. This page collapses all three
into a single sidebar entry; the layouts themselves become tabs in an
``Adw.ViewSwitcher`` so the *choice between layouts* reads as the
primary action.

The Dwindle/Master/Scrolling schema groups are marked ``hidden: true``
with ``parent_page: "layouts"`` (see ``data/schema/options.json``), so
their option keys route here automatically for badge counts and search
navigation.
"""

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from hyprmod.core import schema
from hyprmod.ui import make_page_layout

if TYPE_CHECKING:
    from hyprmod.window import HyprModWindow


# Order matters — defines the ViewSwitcher tab order. Layouts whose
# schema groups were dropped by the version guard (e.g. ``scrolling`` on
# Hyprland < 0.50) are silently skipped.
_LAYOUT_IDS: tuple[str, ...] = ("dwindle", "master", "scrolling")

# The ``general:layout`` option's value matches one of the layout group
# ids — used to default the visible tab on first paint.
_ACTIVE_LAYOUT_KEY = "general:layout"


class LayoutsPage:
    """Builds the merged Layouts page with a per-layout view switcher."""

    def __init__(self, window: "HyprModWindow"):
        self._window = window
        self._view_stack: Adw.ViewStack | None = None
        # option_key -> layout_id, populated during build(). Used by
        # ``focus_layout_for_option`` so search-result navigation lands
        # on the correct sub-tab before the option row gets focused.
        self._key_to_layout: dict[str, str] = {}

    def build(self, header: Adw.HeaderBar) -> Adw.ToolbarView:
        toolbar_view, _, content_box, _ = make_page_layout(header=header)

        view_stack = Adw.ViewStack()
        view_stack.set_vexpand(True)

        groups_by_id = {g["id"]: g for g in schema.get_groups(self._window._schema)}

        for layout_id in _LAYOUT_IDS:
            group = groups_by_id.get(layout_id)
            if group is None:
                continue  # layout filtered out by Hyprland version guard
            self._add_layout_tab(view_stack, group)

        # Replace the page header's title widget with a ViewSwitcher
        # tied to the stack we just populated. The title text "Layouts"
        # set by ``_make_page_header`` is implicit — the switcher labels
        # already convey "you're picking a layout."
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(view_stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        content_box.append(view_stack)
        self._view_stack = view_stack

        # Default the visible tab to whichever layout is currently active.
        # Falls back to the first available tab if the active value is
        # ``monocle`` (no settings page) or unknown.
        self._select_default_tab()

        return toolbar_view

    def _add_layout_tab(self, view_stack: Adw.ViewStack, group: dict) -> None:
        """Build one layout's PreferencesGroup widgets as a ViewStack tab."""
        sub_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        sub_box.set_margin_top(12)
        sub_box.set_margin_bottom(12)
        sub_box.set_margin_start(12)
        sub_box.set_margin_end(12)

        # Reuse the window's section-widget builder so the option rows
        # behave identically to standalone schema pages — same OptionRow
        # registration, same dependents, same dirty tracking.
        for pref_group in self._window.build_schema_group_widgets(group["id"]):
            sub_box.append(pref_group)

        # Each tab gets its own scrolled window so a long layout
        # configuration doesn't push other tabs' content off screen.
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(sub_box)
        scrolled.set_vexpand(True)

        icon = group.get("icon", "")
        view_stack.add_titled_with_icon(scrolled, group["id"], group["label"], icon)

        for section in group.get("sections", []):
            for option in section.get("options", []):
                self._key_to_layout[option["key"]] = group["id"]

    def _select_default_tab(self) -> None:
        """Show the active layout's tab on first paint, if it has one."""
        if self._view_stack is None:
            return
        state = self._window.app_state.get(_ACTIVE_LAYOUT_KEY)
        active = str(state.live_value) if state and state.live_value is not None else ""
        if active in self._key_to_layout.values():
            self._view_stack.set_visible_child_name(active)

    def focus_layout(self, layout_id: str) -> None:
        """Switch to *layout_id*'s tab, if available."""
        if self._view_stack is not None and layout_id in self._key_to_layout.values():
            self._view_stack.set_visible_child_name(layout_id)

    def focus_layout_for_option(self, option_key: str) -> None:
        """Switch to whichever layout's tab contains *option_key*.

        Used during search-result navigation so the Layouts page opens
        on the right tab before the search code focuses the option row.
        """
        layout_id = self._key_to_layout.get(option_key)
        if layout_id is not None:
            self.focus_layout(layout_id)
