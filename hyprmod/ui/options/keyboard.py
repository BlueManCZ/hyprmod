"""Keyboard layouts option row: a summary row opening a list editor.

One widget owning two config keys. The ordered list of (layout, variant)
pairs serializes to the positionally-aligned ``kb_layout`` and
``kb_variant`` keys, so multiple layouts and per-layout variants stay
aligned by construction. The first entry is the default layout; a
``kb_options`` ``grp:*`` shortcut switches between them.

The row itself is a compact summary; activating it opens
``KeyboardLayoutsDialog`` to add, remove, and reorder layouts.
"""

from gi.repository import Adw, Gtk

from hyprmod.ui.options.base import OptionRow
from hyprmod.ui.sources import MissingDependencyError, get_source_values

Source = tuple[str, str]


def parse_sources(layout_str: str, variant_str: str) -> list[Source]:
    """Split the two comma-separated config values into ordered pairs.

    Variants align to layouts by comma position; missing or surplus
    variant slots default to empty, and empty layout slots are dropped.
    """
    layouts = layout_str.split(",") if layout_str.strip() else []
    variants = variant_str.split(",") if variant_str.strip() else []
    pairs = []
    for i, layout in enumerate(layouts):
        layout = layout.strip()
        if not layout:
            continue
        variant = variants[i].strip() if i < len(variants) else ""
        pairs.append((layout, variant))
    return pairs


def serialize_sources(sources: list[Source]) -> tuple[str, str]:
    """Join ordered pairs back into ``(kb_layout, kb_variant)`` strings.

    When every variant is empty the variant value collapses to ``""`` so
    the key stays unset rather than writing a row of bare commas.
    """
    layout_str = ",".join(layout for layout, _ in sources)
    variants = [variant for _, variant in sources]
    variant_str = "" if all(v == "" for v in variants) else ",".join(variants)
    return layout_str, variant_str


class KeyboardLayoutsOptionRow(OptionRow):
    """Summary row for the keyboard layouts, owning kb_layout + kb_variant."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        self._variant_key = option["companion_key"]
        self._app_state = None
        self._apply_paired = None
        self._sources: list[Source] = []
        self._unavailable = False

        try:
            self._all_items = get_source_values("xkb_input_sources")
        except MissingDependencyError as exc:
            self._unavailable = True
            self._all_items = []
            self._by_pair: dict[Source, dict] = {}
            row = Adw.ActionRow(title=option.get("label", option["key"]), subtitle=str(exc))
            row.set_sensitive(False)
            super().__init__(row, option, on_change, on_reset, on_discard)
            return

        self._by_pair = {(i["layout"], i["variant"]): i for i in self._all_items}

        row = Adw.ActionRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        row.set_activatable(True)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", self._on_activated)
        super().__init__(row, option, on_change, on_reset, on_discard)

        self._sources = parse_sources(str(value) if value else "", "")
        self._update_summary()

    def bind_state(self, app_state, apply_paired):
        """Wire state reads for both keys and the atomic paired-apply callback."""
        self._app_state = app_state
        self._apply_paired = apply_paired

    # -- Dialog --

    def _on_activated(self, _row):
        # Imported lazily: the dialog pulls helpers from ``hyprmod.ui``'s
        # package init, which is still mid-load when the options factory
        # (this row's importer) runs. By activation time it is ready.
        from hyprmod.ui.keyboard_layouts_dialog import KeyboardLayoutsDialog

        KeyboardLayoutsDialog.present_singleton(
            self.row,
            sources=list(self._sources),
            all_items=self._all_items,
            on_change=self._apply_sources,
        )

    def _apply_sources(self, sources: list[Source]):
        """Live-apply an edited layout list from the dialog."""
        self._sources = sources
        self._emit()
        self._update_summary()

    # -- Rendering --

    def _update_summary(self):
        names = [self._name_for(pair) for pair in self._sources]
        self.row.set_subtitle(", ".join(names) if names else "Not set")  # type: ignore[attr-defined]

    def _name_for(self, pair: Source) -> str:
        item = self._by_pair.get(pair)
        if item:
            return item["name"]
        layout, variant = pair
        return f"{layout} ({variant})" if variant else layout

    # -- Value flow --

    def _emit(self):
        layout_str, variant_str = serialize_sources(self._sources)
        if self._apply_paired is not None:
            self._apply_paired([(self._variant_key, variant_str), (self.key, layout_str)])

    def _read(self, key: str) -> str:
        if self._app_state is None:
            return ""
        state = self._app_state.get(key)
        if state is None or state.live_value is None:
            return ""
        return str(state.live_value)

    def _set_widget_value(self, value):
        # Both keys are re-read from state, so the pushed value is ignored.
        if self._unavailable:
            return
        self._sources = parse_sources(self._read(self.key), self._read(self._variant_key))
        self._update_summary()
        self._refresh_open_dialog()

    def _refresh_open_dialog(self):
        """Keep an open management dialog in sync with external changes (undo/redo)."""
        from hyprmod.ui.keyboard_layouts_dialog import KeyboardLayoutsDialog

        dialog = KeyboardLayoutsDialog.current()
        if isinstance(dialog, KeyboardLayoutsDialog):
            dialog.set_sources(list(self._sources))

    def update_modified_state(self, is_managed=False, is_dirty=False, is_saved=False):
        if self._app_state is not None:
            layout = self._app_state.get(self.key)
            variant = self._app_state.get(self._variant_key)
            is_managed = bool((layout and layout.managed) or (variant and variant.managed))
            is_dirty = bool((layout and layout.is_dirty) or (variant and variant.is_dirty))
            is_saved = bool(
                (layout and layout.saved_managed) or (variant and variant.saved_managed)
            )
        super().update_modified_state(is_managed, is_dirty, is_saved)

    def _do_reset(self):
        self._on_reset(self._variant_key, "")
        self._on_reset(self.key, self.default_value)

    def _do_discard(self):
        if self._on_discard_single:
            self._on_discard_single(self._variant_key)
            self._on_discard_single(self.key)
