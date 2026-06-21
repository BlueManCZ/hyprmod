"""Dialogs for managing the ordered list of keyboard input sources.

``KeyboardLayoutsDialog`` is the management surface opened from the
Keyboard layouts row: an ordered, reorderable list of (layout, variant)
sources. Adding is a two-step flow, both searchable single-choice
pickers: first a base layout, then a variant for it (skipped when the
layout has none). Activating an existing row reopens the variant picker
to edit it. Everything applies live, the same way every other option in
hyprmod does, so there is no separate commit step.
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.ui import make_inline_hint
from hyprmod.ui.dialog import SingletonDialogMixin
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.reorder import RowReorderController

Source = tuple[str, str]


class _SearchablePicker(SingletonDialogMixin, Adw.Dialog):
    """Searchable single-choice list that calls ``on_pick(value)`` then closes.

    Each item is a dict with ``title``, ``subtitle``, ``search`` (lowercased
    haystack), ``value`` (passed back to ``on_pick``), and optional
    ``current`` (marks the active choice with a check).
    """

    def __init__(
        self,
        *,
        title: str,
        placeholder: str,
        items: list[dict],
        on_pick: Callable[[str], None],
    ):
        super().__init__()
        self._on_pick = on_pick
        self._filter_term = ""

        self.set_title(title)
        self.set_content_width(440)
        self.set_content_height(540)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(placeholder)
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

        self._empty = EmptyState(
            title="No Matches",
            description="Try a different search term.",
            icon_name="system-search-symbolic",
        )
        self._empty.set_visible(False)
        body.append(self._empty)

        toolbar.set_content(body)
        self.set_child(toolbar)

        for item in items:
            self._list_box.append(self._make_row(item))
        self._list_box.invalidate_filter()
        self._update_empty_state()
        self._search_entry.grab_focus()

    def _make_row(self, item: dict) -> Gtk.ListBoxRow:
        row = Adw.ActionRow(title=item["title"], subtitle=item.get("subtitle", ""))
        row.set_activatable(True)
        if item.get("current"):
            row.add_suffix(Gtk.Image.new_from_icon_name("check-plain-symbolic"))
        row.connect("activated", lambda _r, v=item["value"]: self._activate(v))
        row.search_text = item.get("search", "")  # type: ignore[attr-defined]
        return row

    def _activate(self, value: str) -> None:
        self.close()
        self._on_pick(value)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._filter_term = entry.get_text().strip().lower()
        self._list_box.invalidate_filter()
        self._update_empty_state()

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        if not self._filter_term:
            return True
        return self._filter_term in getattr(row, "search_text", "")

    def _update_empty_state(self) -> None:
        any_visible = False
        child = self._list_box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ListBoxRow) and self._filter_row(child):
                any_visible = True
                break
            child = child.get_next_sibling()
        self._empty.set_visible(not any_visible)
        self._list_box.set_visible(any_visible)


class LayoutPickerDialog(_SearchablePicker):
    """Step 1: pick a base layout. Its own subclass so the singleton slot
    is independent of the variant picker that opens next."""


class VariantPickerDialog(_SearchablePicker):
    """Step 2 and edit: pick a variant for a chosen layout."""


class KeyboardLayoutsDialog(SingletonDialogMixin, Adw.Dialog):
    """Reorderable list of keyboard input sources, applied live.

    Parameters
    ----------
    sources:
        Current ordered ``(layout, variant)`` pairs.
    all_items:
        The full ``xkb_input_sources`` catalog (for labels + the pickers).
    on_change:
        Called with the new ordered list after every add, edit, remove, or
        reorder. The caller serializes it to ``kb_layout`` / ``kb_variant``
        and applies it.
    """

    def __init__(
        self,
        *,
        sources: list[Source],
        all_items: list[dict],
        on_change: Callable[[list[Source]], None],
    ):
        super().__init__()
        self._sources = list(sources)
        self._all_items = all_items
        self._by_pair = {(i["layout"], i["variant"]): i for i in all_items}
        self._variants_by_layout: dict[str, list[dict]] = {}
        for item in all_items:
            self._variants_by_layout.setdefault(item["layout"], []).append(item)
        self._on_change = on_change
        self._rows: list[Adw.ActionRow] = []
        self._reorder = RowReorderController(move=self._move, iter_rows=lambda: self._rows)

        self.set_title("Keyboard layouts")
        self.set_content_width(460)
        self.set_content_height(520)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        self._hint = make_inline_hint(
            "Reorder by dragging a layout, or with Alt+↑ / Alt+↓ on a focused row. "
            "The first layout is the default."
        )
        body.append(self._hint)

        self._group = Adw.PreferencesGroup()
        body.append(self._group)

        add_button = Gtk.Button()
        add_button.set_halign(Gtk.Align.START)
        add_button.add_css_class("flat")
        add_button.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add layout"))
        add_button.connect("clicked", self._on_add_clicked)
        body.append(add_button)

        body.append(
            make_inline_hint(
                "Set a switch shortcut (e.g. grp:alt_shift_toggle) under Keyboard options.",
                icon_name="keyboard-shortcuts-symbolic",
            )
        )

        toolbar.set_content(body)
        self.set_child(toolbar)
        self._rebuild()

    # ── Catalog lookups ──

    def _layout_name(self, layout: str) -> str:
        for entry in self._variants_by_layout.get(layout, []):
            if entry["variant"] == "":
                return entry["name"]
        return layout

    def _has_variants(self, layout: str) -> bool:
        return any(entry["variant"] for entry in self._variants_by_layout.get(layout, []))

    # ── Rendering ──

    def set_sources(self, sources: list[Source]) -> None:
        """Replace the displayed list after an external change (undo/redo).

        Skips when already in sync so a dialog-initiated edit (which round-trips
        back through here) does not rebuild twice or fight an in-progress drag.
        """
        if list(sources) == self._sources:
            return
        self._sources = list(sources)
        self._rebuild()

    def _rebuild(self, focus_idx: int | None = None) -> None:
        for row in self._rows:
            self._group.remove(row)
        self._rows = []

        self._hint.set_visible(len(self._sources) >= 2)

        for idx, pair in enumerate(self._sources):
            row = self._make_row(idx, pair)
            self._group.add(row)
            self._rows.append(row)

        if focus_idx is not None and 0 <= focus_idx < len(self._rows):
            self._rows[focus_idx].grab_focus()

    def _make_row(self, idx: int, pair: Source) -> Adw.ActionRow:
        item = self._by_pair.get(pair)
        layout, variant = pair
        name = item["name"] if item else (f"{layout} ({variant})" if variant else layout)
        subtitle = item["id"] if item else (f"{layout}+{variant}" if variant else layout)

        row = Adw.ActionRow(title=name, subtitle=subtitle)

        if idx == 0:
            badge = Gtk.Label(label="Default")
            badge.add_css_class("dim-label")
            badge.add_css_class("caption")
            row.add_suffix(badge)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove layout")
        remove.set_sensitive(len(self._sources) > 1)
        remove.connect("clicked", self._on_remove, idx)
        row.add_suffix(remove)

        # Only layouts that actually have variants are worth opening to edit.
        if self._has_variants(layout):
            row.set_activatable(True)
            row.connect("activated", self._on_edit, idx)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))

        self._reorder.attach(row, idx)
        return row

    # ── Mutations (each applies live) ──

    def _apply(self, focus_idx: int | None = None) -> None:
        self._rebuild(focus_idx=focus_idx)
        self._on_change(list(self._sources))

    def _on_remove(self, _button, idx: int) -> None:
        if len(self._sources) > 1 and 0 <= idx < len(self._sources):
            del self._sources[idx]
            self._apply()

    def _add_source(self, layout: str, variant: str) -> None:
        self._sources.append((layout, variant))
        self._apply()

    def _set_variant(self, idx: int, variant: str) -> None:
        if 0 <= idx < len(self._sources):
            self._sources[idx] = (self._sources[idx][0], variant)
            self._apply()

    def _move(self, src: int, target: int) -> bool:
        n = len(self._sources)
        if src == target or not (0 <= src < n and 0 <= target < n):
            return False
        self._sources.insert(target, self._sources.pop(src))
        self._apply(focus_idx=target)
        return True

    # ── Add / edit (two-step pickers) ──

    def _on_add_clicked(self, _button) -> None:
        items = [
            {
                "title": entry["name"],
                "subtitle": entry["layout"],
                "search": entry["label"].lower(),
                "value": entry["layout"],
            }
            for entry in sorted(
                (i for i in self._all_items if i["variant"] == ""),
                key=lambda i: i["name"].casefold(),
            )
        ]
        LayoutPickerDialog.present_singleton(
            self,
            title="Add layout",
            placeholder="Search layouts…",
            items=items,
            on_pick=self._on_layout_picked,
        )

    def _on_layout_picked(self, layout: str) -> None:
        if self._has_variants(layout):
            self._open_variant_picker(
                layout, current=None, on_pick=lambda v: self._add_source(layout, v)
            )
        elif (layout, "") not in set(self._sources):
            self._add_source(layout, "")

    def _on_edit(self, _row, idx: int) -> None:
        layout, variant = self._sources[idx]
        self._open_variant_picker(
            layout, current=variant, on_pick=lambda v: self._set_variant(idx, v)
        )

    def _open_variant_picker(
        self, layout: str, *, current: str | None, on_pick: Callable[[str], None]
    ) -> None:
        exclude = set(self._sources)
        items = []
        # Default (the bare layout) first, then variants alphabetically.
        entries = sorted(
            self._variants_by_layout.get(layout, []),
            key=lambda e: (e["variant"] != "", e["name"].casefold()),
        )
        for entry in entries:
            is_current = entry["variant"] == current
            if (layout, entry["variant"]) in exclude and not is_current:
                continue
            items.append(
                {
                    "title": "Default" if entry["variant"] == "" else entry["name"],
                    "subtitle": entry["id"],
                    "search": entry["label"].lower(),
                    "value": entry["variant"],
                    "current": is_current,
                }
            )
        VariantPickerDialog.present_singleton(
            self,
            title=self._layout_name(layout),
            placeholder="Search variants…",
            items=items,
            on_pick=on_pick,
        )


__all__ = ["KeyboardLayoutsDialog", "LayoutPickerDialog", "VariantPickerDialog"]
