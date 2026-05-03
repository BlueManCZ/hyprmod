"""Header-bar chip showing the pending-changes count.

Replaces the old ``Pending Changes`` sidebar row. The chip is visible
from every page header so users always see the unsaved-change count
regardless of where they are in the app, and clicking it navigates to
the Pending Changes page.

A separate instance lives on each page header, kept in sync via
:class:`PendingChipGroup` — the chip on the Pending Changes page itself
is omitted (it would be a no-op).
"""

from collections.abc import Callable

from gi.repository import Gtk

from hyprmod.ui.icons import PENDING_ICON


class PendingChip(Gtk.Button):
    """A flat header-bar button showing ``[icon] N`` while N > 0."""

    def __init__(self, on_click: Callable[[], None]):
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("pending-chip")
        self.set_tooltip_text("View pending changes")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name(PENDING_ICON))
        self._label = Gtk.Label()
        box.append(self._label)
        self.set_child(box)

        self.connect("clicked", lambda _btn: on_click())
        self.set_visible(False)

    def set_count(self, count: int) -> None:
        """Show the chip with *count* labelled, or hide it when zero."""
        if count > 0:
            self._label.set_label(str(count))
            self.set_visible(True)
        else:
            self.set_visible(False)


class PendingChipGroup:
    """Keeps multiple :class:`PendingChip` instances in sync.

    The window builds a chip for every page header (except the Pending
    Changes page itself). Updating the count flows through this group
    so all visible chips stay consistent — only one is on screen at a
    time, but using a group keeps the wire-up simple and avoids leaking
    the chip list to every caller.
    """

    def __init__(self, on_click: Callable[[], None]):
        self._on_click = on_click
        self._chips: list[PendingChip] = []
        self._count = 0

    def new_chip(self) -> PendingChip:
        """Create a new chip bound to this group's click handler and count."""
        chip = PendingChip(self._on_click)
        chip.set_count(self._count)
        self._chips.append(chip)
        return chip

    def set_count(self, count: int) -> None:
        """Update the count shown on every chip in this group."""
        self._count = count
        for chip in self._chips:
            chip.set_count(count)
