"""Combo (dropdown) option rows: static choices and dynamic-source choices."""

from gi.repository import Adw, Gtk

from hyprmod.ui.managed_row import make_combo_row
from hyprmod.ui.options.base import OptionRow
from hyprmod.ui.sources import MissingDependencyError, get_source_values


def _is_int(s: str) -> bool:
    """Return True if *s* looks like an integer (including negative)."""
    return s.lstrip("-").isdigit() if s else False


def _wrapping_label_setup(_factory, list_item):
    """Factory setup callback: create a wrapping label for dropdown items."""
    label = Gtk.Label(xalign=0)
    label.set_wrap(True)
    label.set_margin_top(6)
    label.set_margin_bottom(6)
    label.set_margin_start(6)
    label.set_margin_end(6)
    list_item.set_child(label)


class ComboOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        values = option.get("values", [])
        self._labels = [v["label"] for v in values]
        self._ids = [v.get("id", str(i)) for i, v in enumerate(values)]

        current_str = str(value) if value is not None else str(option.get("default", ""))
        selected = self._ids.index(current_str) if current_str in self._ids else 0
        row = make_combo_row(
            option.get("label", option["key"]),
            subtitle=option.get("description", ""),
            model=Gtk.StringList.new(self._labels),
            selected=selected,
        )

        # IDs are always strings (from JSON schema), but live values from IPC
        # may be int.  Coerce emitted IDs to match so dirty checks work.
        self._coerce = int if all(_is_int(i) for i in self._ids) else None

        super().__init__(row, option, on_change, on_reset, on_discard)

        def on_selected_changed(row_, _pspec):
            idx = row_.get_selected()
            if 0 <= idx < len(self._ids):
                val = self._ids[idx]
                if self._coerce is not None:
                    try:
                        val = self._coerce(val)
                    except (ValueError, TypeError):
                        pass
                self._emit_change(val)

        self._change_handler_id = row.connect("notify::selected", on_selected_changed)

    def _set_widget_value(self, value):
        val_str = str(value) if value is not None else ""
        if val_str in self._ids:
            self.row.set_selected(self._ids.index(val_str))  # type: ignore[attr-defined]


class SourceComboOptionRow(OptionRow):
    """ComboRow whose values come from a dynamic source, with search enabled."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        self._source_name = option["source"]
        self._source_args = dict(option.get("source_args", {}))

        self._labels = []
        self._ids = []

        row = Adw.ComboRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        row.set_enable_search(True)
        row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        row.add_css_class("wide-dropdown")
        row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))

        # Custom list factory so dropdown labels don't truncate
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", _wrapping_label_setup)
        factory.connect("bind", self._on_factory_bind)
        row.set_list_factory(factory)

        super().__init__(row, option, on_change, on_reset, on_discard)

        self._populate(value)

        def on_selected_changed(row_, _pspec):
            idx = row_.get_selected()
            if 0 <= idx < len(self._ids):
                self._emit_change(self._ids[idx])

        self._change_handler_id = row.connect("notify::selected", on_selected_changed)

    def _populate(self, select_value=None):
        """Rebuild the dropdown items from the source."""
        try:
            values = get_source_values(self._source_name, **self._source_args)
        except MissingDependencyError as exc:
            self.row.set_subtitle(exc.message)  # type: ignore[union-attr]
            self.row.set_sensitive(False)
            return
        self._labels = [v["label"] for v in values]
        self._ids = [v["id"] for v in values]

        if self._change_handler_id is not None:
            self.row.handler_block(self._change_handler_id)  # type: ignore[attr-defined]
        try:
            self.row.set_model(Gtk.StringList.new(self._labels))  # type: ignore[attr-defined]
            current_str = "" if select_value is None else str(select_value)
            try:
                idx = self._ids.index(current_str)
            except ValueError:
                idx = None
            if idx is not None:
                self.row.set_selected(idx)  # type: ignore[attr-defined]
        finally:
            if self._change_handler_id is not None:
                self.row.handler_unblock(self._change_handler_id)  # type: ignore[attr-defined]

    def refresh_source(self, **kwargs):
        """Re-populate the dropdown with updated source args."""
        self._source_args.update(kwargs)
        idx = self.row.get_selected()  # type: ignore[attr-defined]
        prev = self._ids[idx] if 0 <= idx < len(self._ids) else None
        self._populate(select_value=prev)
        new_idx = self.row.get_selected()  # type: ignore[attr-defined]
        new = self._ids[new_idx] if 0 <= new_idx < len(self._ids) else None
        if new is not None and new != prev:
            self._emit_change(new)

    @staticmethod
    def _on_factory_bind(_factory, list_item):
        label = list_item.get_child()
        item = list_item.get_item()
        label.set_label(item.get_string())

    def _set_widget_value(self, value):
        val_str = str(value) if value is not None else ""
        if val_str in self._ids:
            self.row.set_selected(self._ids.index(val_str))  # type: ignore[attr-defined]
